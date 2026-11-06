"""External providers only. No SQL, CSV, HTTP server, or UI dependencies."""
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
import truststore
truststore.inject_into_ssl()
from openchart_option_chain import NSEOptionChain
from nifty_option_chain import flatten_chain, select_strike_range
from option_rsi import add_tradingview_rsi, add_tradingview_option_rsi


def fetch_snapshot():
    client = NSEOptionChain()
    try:
        client.connect()
        expiry = client.available_expiries()[0]
        raw = client.option_chain(expiry)
    finally:
        client.session.close()
    collected_at = datetime.now(timezone.utc)
    rows = flatten_chain(raw, expiry)
    spot, _, _, selected = select_strike_range(raw, rows)
    diagnostics = StringIO()
    with redirect_stderr(diagnostics):
        index_rsi = 'N/A'
        try:
            index_rsi = add_tradingview_rsi(selected)
            add_tradingview_option_rsi(selected, expiry)
        except Exception as exc:
            print(f'RSI unavailable: {exc}', file=diagnostics)
    enrichment = {str(row['strikePrice']): row for row in selected}
    for row in rows:
        if str(row['strikePrice']) in enrichment:
            row.update(enrichment[str(row['strikePrice'])])
    return {'collected_at': collected_at, 'market_timestamp': raw['records'].get('timestamp', ''),
            'expiry': expiry, 'spot': spot, 'rows': rows, 'index_rsi': index_rsi,
            'raw_payload': raw, 'warnings': diagnostics.getvalue().splitlines()}
