"""PostgreSQL repository. Every snapshot and all strike rows commit atomically."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .config import database_url


def connect(reader=False, **kwargs):
    return psycopg.connect(database_url(reader), connect_timeout=5, row_factory=dict_row, **kwargs)


def numeric(value):
    if value is None or value == '' or value == 'N/A':
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def save_snapshot(snapshot):
    snapshot_id = uuid.UUID(snapshot['id']) if snapshot.get('id') else uuid.uuid4()
    expiry = datetime.strptime(snapshot['expiry'], '%d-%b-%Y').date()
    with connect() as conn:
        conn.execute('INSERT INTO snapshots(id,collected_at,market_timestamp,expiry,spot,index_rsi,raw_payload,warnings) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                     (snapshot_id, snapshot['collected_at'], snapshot['market_timestamp'], expiry,
                      snapshot['spot'], numeric(snapshot['index_rsi']), Jsonb(snapshot['raw_payload']), Jsonb(snapshot['warnings'])))
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO snapshot_rows VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                               [(snapshot_id, row['strikePrice'], datetime.strptime(row['expiryDate'], '%d-%b-%Y').date(), Jsonb(row)) for row in snapshot['rows']])
            quotes = []
            for row in snapshot['rows']:
                for side in ('CE', 'PE'):
                    if not any(row.get(f'{side}_{field}') not in ('', None) for field in ('lastPrice', 'openInterest', 'totalTradedVolume')):
                        continue
                    quotes.append((snapshot_id, row['strikePrice'], datetime.strptime(row['expiryDate'], '%d-%b-%Y').date(), side,
                                   numeric(row.get(f'{side}_lastPrice')), numeric(row.get(f'{side}_openInterest')),
                                   numeric(row.get(f'{side}_totalTradedVolume')), numeric(row.get(f'{side}_impliedVolatility')),
                                   numeric(row.get(f'{side}_RSI14_15m')), Jsonb({key: value for key, value in row.items() if key.startswith(side+'_')})))
            cursor.executemany('INSERT INTO option_quotes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', quotes)
    return str(snapshot_id)


def state_update(**values):
    with connect() as conn:
        conn.execute('UPDATE collector_state SET payload=payload || %s, heartbeat=now() WHERE id=1', (Jsonb(values),))


def get_snapshot(snapshot_id=None, full=False):
    with connect(True) as conn:
        meta = conn.execute('SELECT * FROM snapshots WHERE id=%s', (snapshot_id,)).fetchone() if snapshot_id else conn.execute('SELECT * FROM snapshots ORDER BY collected_at DESC,id DESC LIMIT 1').fetchone()
        if not meta:
            return None
        query = 'SELECT payload FROM snapshot_rows WHERE snapshot_id=%s'
        args = [meta['id']]
        if not full:
            query += ' AND strike BETWEEN %s AND %s'
            args.extend([meta['spot']-600, meta['spot']+600])
        rows = [r['payload'] for r in conn.execute(query+' ORDER BY strike', args)]
    return {'id': str(meta['id']), 'rows': rows, 'spot': float(meta['spot']),
            'expiry': meta['expiry'].strftime('%d-%b-%Y'), 'market_timestamp': meta['market_timestamp'],
            'index_rsi': float(meta['index_rsi']) if meta['index_rsi'] is not None else 'N/A',
            'filename': f"nifty_snapshot_{meta['id']}.csv", 'collected_at': meta['collected_at'].isoformat(), 'warnings': meta['warnings']}


def runtime_state():
    with connect(True) as conn:
        row = conn.execute('SELECT payload,heartbeat FROM collector_state WHERE id=1').fetchone()
    state = dict(row['payload'])
    state.pop('market_open', None)
    state.pop('schedule_reason', None)
    state.setdefault('collector_available', False)
    state.setdefault('collection_status', 'Collector has not started')
    stale = (datetime.now(timezone.utc)-row['heartbeat']).total_seconds() > (180 if state.get('loading') else 30)
    if stale:
        state.update(loading=False, collector_available=False, collection_status='Collector offline', error='Collector is offline; showing the last stored snapshot.', next_refresh=None)
    return state


def get_920_baseline(day=None):
    """Reuse permanently stored snapshots: first fresh NSE capture in 09:20 IST.

    Both capture time and NSE timestamp must fall in that day's 09:20 minute.
    A later or stale quote must never be labelled as the 09:20 reference.
    """
    ist = ZoneInfo('Asia/Kolkata')
    day = day or datetime.now(ist).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=ist).replace(hour=9, minute=20)
    with connect(True) as conn:
        candidates = conn.execute(
            'SELECT id,market_timestamp FROM snapshots WHERE symbol=%s AND collected_at >= %s AND collected_at < %s ORDER BY collected_at,id',
            ('NIFTY', start, start + timedelta(minutes=1))).fetchall()
    for candidate in candidates:
        try:
            market = datetime.strptime(candidate['market_timestamp'], '%d-%b-%Y %H:%M:%S')
        except (ValueError, TypeError):
            continue
        if market.date() == day and (market.hour, market.minute) == (9, 20):
            return {'day': day.isoformat(), 'snapshot': get_snapshot(candidate['id'], full=True)}
    return {'day': day.isoformat(), 'snapshot': None}


def summarize_close(snapshot):
    """Rank distinct positive levels (including ties) and total all stored strikes."""
    sides = {}
    for side in ('CE', 'PE'):
        metrics = {}
        for name, field in (('volume', 'totalTradedVolume'), ('oi', 'openInterest')):
            levels = {}
            valid = []
            for row in snapshot['rows']:
                value = numeric(row.get(side+'_'+field))
                if value is None or value < 0:
                    continue
                valid.append(value)
                if value > 0:
                    levels.setdefault(value, []).append(row['strikePrice'])
            metrics[name] = {'total': sum(valid) if valid else None,
                             'available_strikes': len(valid),
                             'top': [{'value': value, 'strikes': sorted(levels[value], key=float)}
                                     for value in sorted(levels, reverse=True)[:2]]}
        sides[side] = metrics
    combined = {}
    for metric in ('volume', 'oi'):
        totals = [sides[side][metric]['total'] for side in ('CE', 'PE')]
        combined[metric] = sum(totals) if all(v is not None for v in totals) else None
    return {'snapshot': {k: snapshot[k] for k in ('id', 'expiry', 'spot', 'market_timestamp', 'collected_at')},
            'strike_count': len(snapshot['rows']), 'sides': sides, 'combined': combined}


def get_previous_close(today=None):
    """Latest available prior session with an NSE closing quote; never intraday."""
    today = today or datetime.now(ZoneInfo('Asia/Kolkata')).date()
    best = None
    with connect(True) as conn:
        # Continuous off-hours captures repeat exchange timestamps. Deduplicate
        # those in SQL and rank by the parsed exchange date, not capture date.
        candidates = conn.execute("""
            SELECT DISTINCT ON (market_timestamp) id,market_timestamp,collected_at
            FROM snapshots WHERE symbol='NIFTY' AND market_timestamp ~
                '^[0-9]{2}-[A-Za-z]{3}-[0-9]{4} 15:[3-5][0-9]:[0-5][0-9]$'
            ORDER BY market_timestamp,collected_at DESC,id DESC
        """).fetchall()
    for candidate in candidates:
        try:
            stamp = datetime.strptime(candidate['market_timestamp'], '%d-%b-%Y %H:%M:%S')
        except (TypeError, ValueError):
            continue
        if stamp.date() >= today:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, candidate['id'])
    if best is None:
        return {'as_of_day': today.isoformat(), 'summary': None}
    summary = summarize_close(get_snapshot(best[1], full=True))
    summary['session_day'] = best[0].date().isoformat()
    return {'as_of_day': today.isoformat(), 'summary': summary}


@lru_cache(maxsize=8)
def get_volume_shifts(snapshot_id):
    """Latest volume-leader change per side for this capture day and expiry.

    Compare the same current strike window throughout; moving the display window
    alone must not be interpreted as a volume transfer. All values are from SQL.
    """
    with connect(True) as conn:
        meta = conn.execute('SELECT id,collected_at,expiry,spot FROM snapshots WHERE id=%s', (snapshot_id,)).fetchone()
        if not meta:
            return {}
        start = meta['collected_at'].astimezone(ZoneInfo('Asia/Kolkata')).replace(hour=0, minute=0, second=0, microsecond=0)
        shifts = conn.execute("""
            WITH ranked AS (
                SELECT s.id,s.collected_at,q.side,q.strike,q.volume,
                    max(q.volume) OVER (PARTITION BY s.id,q.side) AS maximum
                FROM snapshots s JOIN option_quotes q ON q.snapshot_id=s.id
                WHERE s.collected_at >= %s AND (s.collected_at,s.id) <= (%s,%s)
                    AND s.expiry=%s AND s.symbol='NIFTY' AND q.expiry=s.expiry
                    AND q.strike BETWEEN %s AND %s AND q.volume>0
            ), leaders AS (
                SELECT id,collected_at,side,array_agg(strike ORDER BY strike) AS strikes
                FROM ranked WHERE volume=maximum GROUP BY id,collected_at,side
            ), changes AS (
                SELECT *,lag(strikes) OVER (PARTITION BY side ORDER BY collected_at,id) AS previous
                FROM leaders
            )
            SELECT DISTINCT ON (side) side,collected_at,strikes,previous FROM changes
            WHERE previous IS NOT NULL AND previous<>strikes
            ORDER BY side,collected_at DESC,id DESC
        """, (start,meta['collected_at'],meta['id'],meta['expiry'],meta['spot']-600,meta['spot']+600)).fetchall()
    result = {}
    for change in shifts:
        before, after = change['previous'], change['strikes']
        if min(after) > max(before):
            direction = 'SHIFTED FROM BOTTOM TO TOP'
        elif max(after) < min(before):
            direction = 'SHIFTED FROM TOP TO BOTTOM'
        else:
            direction = 'VOLUME LEADER TIE CHANGED'
        result[change['side']] = {'direction': direction, 'from_strikes': before,
                                  'to_strikes': after, 'at': change['collected_at'].isoformat()}
    return result


@lru_cache(maxsize=8)
def get_strength_transitions(snapshot_id):
    """Latest WTT/WTB -> STRONG event, retained while that strong state lasts."""
    with connect(True) as conn:
        meta = conn.execute('SELECT id,collected_at,expiry,spot FROM snapshots WHERE id=%s', (snapshot_id,)).fetchone()
        if not meta:
            return {}
        start = meta['collected_at'].astimezone(ZoneInfo('Asia/Kolkata')).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = conn.execute("""
            WITH ranked AS (
                SELECT s.id,s.collected_at,q.side,q.strike,q.volume,
                    dense_rank() OVER (PARTITION BY s.id,q.side ORDER BY q.volume DESC) AS rank
                FROM snapshots s JOIN option_quotes q ON q.snapshot_id=s.id
                WHERE s.collected_at >= %s AND (s.collected_at,s.id) <= (%s,%s)
                    AND s.expiry=%s AND s.symbol='NIFTY' AND q.expiry=s.expiry
                    AND q.strike BETWEEN %s AND %s AND q.volume>0
            )
            SELECT id,collected_at,side,rank,volume,array_agg(strike ORDER BY strike) AS strikes
            FROM ranked WHERE rank<=2 GROUP BY id,collected_at,side,rank,volume
            ORDER BY collected_at,id,side,rank
        """, (start,meta['collected_at'],meta['id'],meta['expiry'],meta['spot']-600,meta['spot']+600)).fetchall()
    history = {}
    for row in rows:
        history.setdefault((row['id'],row['collected_at']), {}).setdefault(row['side'], []).append(row)
    previous, events = {}, {}
    for (_, captured), sides in history.items():
        for side in ('CE', 'PE'):
            levels = sides.get(side, [])
            state = None
            if len(levels) == 2:
                highest, second = levels
                # Compare exact volumes: 75% itself is neutral, never STRONG.
                if second['volume']*4 < highest['volume']*3:
                    state = 'STRONG'
                elif second['volume']*4 > highest['volume']*3:
                    if min(second['strikes']) > max(highest['strikes']):
                        state = 'WTT'
                    elif max(second['strikes']) < min(highest['strikes']):
                        state = 'WTB'
            old = previous.get(side)
            if state == 'STRONG':
                if old and old['state'] in ('WTT', 'WTB'):
                    events[side] = {'from_status': old['state'], 'to_status': 'STRONG',
                                    'from_strikes': old['strikes'], 'to_strikes': levels[0]['strikes'],
                                    'at': captured.isoformat()}
            else:
                events.pop(side, None)
            # Keep the last WTT/WTB across a neutral 75% boundary, but do not
            # carry a STRONG event across missing or mixed data.
            if state is not None:
                previous[side] = {'state': state, 'strikes': levels[0]['strikes']}
            elif len(levels) != 2 or levels[1]['volume']*4 != levels[0]['volume']*3:
                previous.pop(side, None)
    return events
