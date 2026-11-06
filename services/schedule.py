"""NSE F&O regular-session gate. Missing/stale calendars fail closed."""
from datetime import datetime, time, timedelta, timezone
from .config import IST


def market_gate(now, holidays, verified_year):
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return False, 'Weekend — collection paused'
    if verified_year != local.year or holidays is None:
        return False, 'NSE holiday calendar unavailable — collection paused'
    if local.date().isoformat() in holidays:
        return False, 'NSE F&O trading holiday — collection paused'
    clock = local.time().replace(tzinfo=None)
    # [09:15, 15:30): no new request is issued at or after the closing time.
    if not time(9, 15) <= clock < time(15, 30):
        return False, 'Outside 09:15–15:30 IST — collection paused'
    return True, 'NSE regular trading session'


def load_calendar(year):
    from .store import connect
    with connect() as conn:
        row = conn.execute('SELECT * FROM trading_calendars WHERE year=%s', (year,)).fetchone()
    # Refresh daily to pick up exchange-declared changes.
    if row and datetime.now(timezone.utc)-row['fetched_at'] < timedelta(hours=24):
        return row['holidays'], year
    import truststore
    truststore.inject_into_ssl()
    from openchart_option_chain import NSEOptionChain
    client = NSEOptionChain()
    try:
        client.connect()
        payload = client._get_option_json('holiday-master', {'type': 'trading'})
        entries = payload.get('FO')
        if not isinstance(entries, list) or not entries:
            raise ValueError('NSE did not return the F&O holiday calendar')
        dates = [datetime.strptime(entry['tradingDate'], '%d-%b-%Y').date() for entry in entries]
        matching = sorted({date.isoformat() for date in dates if date.year == year})
        if not matching:
            raise ValueError(f'NSE calendar does not cover {year}')
        from psycopg.types.json import Jsonb
        with connect() as conn:
            conn.execute('INSERT INTO trading_calendars(year,holidays,source) VALUES (%s,%s,%s) ON CONFLICT(year) DO UPDATE SET holidays=excluded.holidays,source=excluded.source,fetched_at=now()',
                         (year, Jsonb(matching), 'https://www.nseindia.com/api/holiday-master?type=trading (FO)'))
        return matching, year
    finally:
        client.session.close()
