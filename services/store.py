"""PostgreSQL repository. Every snapshot and all strike rows commit atomically."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
    stale = (datetime.now(timezone.utc)-row['heartbeat']).total_seconds() > (180 if state.get('loading') else 30)
    if stale:
        state.update(loading=False, market_open=False, schedule_reason='Collector offline', error='Collector is offline; showing the last stored snapshot.', next_refresh=None)
    return state
