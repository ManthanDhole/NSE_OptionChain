"""Wilder RSI for exact NIFTY option contracts using Yahoo or OpenChart candles."""

from datetime import datetime, timedelta, timezone
import math
import re
import sys
import time


def wilder_rsi(closes, period=14):
    """Return latest RSI; a flat series is neutral (50)."""
    values = [float(value) for value in closes]
    if len(values) <= period or any(not math.isfinite(value) for value in values):
        return None
    changes = [b - a for a, b in zip(values, values[1:])]
    gain = sum(max(change, 0) for change in changes[:period]) / period
    loss = sum(max(-change, 0) for change in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    if gain == loss == 0:
        return 50.0
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def add_option_rsi(client, rows, expiry):
    """Attach RSI(14, 15m) using exact expiry/strike/side matches only.

    OpenChart search may return only a subset of contracts. Unmatched or
    unavailable contracts retain N/A; never substitute another contract.
    OpenChart exposes historical timestamps as naive UTC. Exclude the current
    fifteen-minute bucket so RSI uses completed candles.
    """
    end = datetime.now(timezone.utc)
    cutoff = end.replace(minute=end.minute // 15 * 15, second=0, microsecond=0)
    start = end - timedelta(days=10)
    target = datetime.strptime(expiry, "%d-%b-%Y").date()
    for row in rows:
        for side in ("CE", "PE"):
            row[f"{side}_RSI14_15m"] = "N/A"
            row[f"{side}_RSI_asof_UTC"] = ""
            row[f"{side}_RSI_source"] = "openchart"
            row[f"{side}_RSI_symbol"] = ""
    contracts = client.search("NIFTY", "FO")
    if contracts.empty:
        print("RSI unavailable: OpenChart returned no option contracts.", file=sys.stderr)
        return
    lookup = {}
    pattern = re.compile(r"^NIFTY\s+(\d{1,2}\s+[A-Z]{3}\s+\d{4})\s+(CE|PE)\s+(\d+(?:\.\d+)?)$", re.I)
    for contract in contracts.to_dict("records"):
        if str(contract.get("type", "")).lower() != "options":
            continue
        match = pattern.fullmatch(str(contract.get("description", "")).strip())
        if match and datetime.strptime(match[1], "%d %b %Y").date() == target:
            lookup.setdefault((float(match[3]), match[2].upper()), []).append(contract)
    available = 0
    for row in rows:
        for side in ("CE", "PE"):
            matches = lookup.get((float(row["strikePrice"]), side), [])
            if len(matches) != 1:
                continue
            contract = matches[0]
            row[f"{side}_RSI_symbol"] = contract["symbol"]
            time.sleep(0.4)
            try:
                history = client.historical_direct(
                    token=contract["scripcode"], symbol=contract["symbol"],
                    symbol_type="Options", start=start, end=cutoff, interval="15m",
                )
                if history.empty:
                    continue
                history = history.sort_index()
                history = history[~history.index.duplicated(keep="last")]
                history = history[history.index < cutoff.replace(tzinfo=None)]
                value = wilder_rsi(history["Close"])
                if value is not None:
                    row[f"{side}_RSI14_15m"] = round(value, 2)
                    row[f"{side}_RSI_asof_UTC"] = str(history.index[-1])
                    available += 1
            except (ValueError, KeyError, TypeError, OSError) as exc:
                print(f"RSI unavailable for {contract['symbol']}: {exc}", file=sys.stderr)
    print(f"RSI(14, 15m): {available}/{len(rows) * 2} option legs available; N/A means missing contract/history.", file=sys.stderr)


def add_yfinance_rsi(rows, expiry):
    """Calculate per-contract RSI from Yahoo's advertised NIFTY option symbols.

    Never substitute ^NSEI index candles for option-contract candles. Yahoo may
    not publish NIFTY options or intraday history, in which case retain N/A.
    """
    for row in rows:
        for side in ("CE", "PE"):
            row[f"{side}_RSI14_15m"] = "N/A"
            row[f"{side}_RSI_asof_UTC"] = ""
            row[f"{side}_RSI_source"] = "yfinance"
            row[f"{side}_RSI_symbol"] = ""
    try:
        import yfinance as yf
    except ImportError:
        print("RSI unavailable: install yfinance with python3 -m pip install -r requirements.txt", file=sys.stderr)
        return
    target = datetime.strptime(expiry, "%d-%b-%Y").strftime("%Y-%m-%d")
    try:
        nifty = yf.Ticker("^NSEI")
        if target not in nifty.options:
            print(f"Yahoo does not advertise NIFTY options for {target}; RSI remains N/A. Try --rsi-source openchart.", file=sys.stderr)
            return
        chain = nifty.option_chain(target)
    except Exception as exc:
        # Provider failures must not prevent the NSE option table from displaying.
        if "resolving timed out" in str(exc).lower() or "could not resolve" in str(exc).lower():
            print("Yahoo hostname lookup timed out (DNS/network error); Yahoo RSI is unavailable.", file=sys.stderr)
        else:
            print(f"Yahoo option discovery failed: {exc}. RSI remains N/A.", file=sys.stderr)
        return
    now = datetime.now(timezone.utc)
    cutoff = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    available = 0
    for side, contracts in (("CE", chain.calls), ("PE", chain.puts)):
        lookup = {}
        if contracts is not None:
            for contract in contracts.to_dict("records"):
                strike = contract.get("strike")
                symbol = contract.get("contractSymbol")
                if strike is not None and isinstance(symbol, str) and symbol:
                    lookup.setdefault(float(strike), []).append(symbol)
        for row in rows:
            matches = lookup.get(float(row["strikePrice"]), [])
            if len(matches) != 1:
                continue
            symbol = matches[0]
            row[f"{side}_RSI_symbol"] = symbol
            time.sleep(0.4)
            try:
                history = yf.Ticker(symbol).history(
                    period="1mo", interval="15m", auto_adjust=False,
                    actions=False, prepost=False, timeout=20, raise_errors=True,
                )
                if history.empty:
                    continue
                history = history.sort_index()
                history = history[~history.index.duplicated(keep="last")]
                if history.index.tz is None:
                    raise ValueError("Yahoo returned timestamps without a timezone")
                history.index = history.index.tz_convert("UTC")
                # Yahoo intraday timestamps mark candle starts. Discard the open bucket.
                history = history[history.index < cutoff]
                closes = history["Close"].dropna()
                value = wilder_rsi(closes)
                if value is not None:
                    row[f"{side}_RSI14_15m"] = round(value, 2)
                    row[f"{side}_RSI_asof_UTC"] = str(closes.index[-1])
                    available += 1
            except Exception as exc:
                print(f"Yahoo RSI unavailable for {symbol}: {exc}", file=sys.stderr)
    print(f"Yahoo RSI(14, 15m): {available}/{len(rows) * 2} option legs available.", file=sys.stderr)



def add_rsi(rows, expiry, source, client_factory):
    """Try Yahoo first in auto mode, filling only missing legs from OpenChart."""
    if source in ("auto", "yfinance"):
        add_yfinance_rsi(rows, expiry)
    if source == "yfinance":
        return
    pending = [row for row in rows if source == "openchart" or any(
        row.get(f"{side}_RSI14_15m", "N/A") == "N/A" for side in ("CE", "PE")
    )]
    if not pending:
        return
    if source == "auto":
        print("Trying OpenChart for missing option RSI values...", file=sys.stderr)
    fallback = [dict(row) for row in pending]
    # Initialize before network calls so failures still leave a usable option table.
    for row in fallback:
        for side in ("CE", "PE"):
            row[f"{side}_RSI14_15m"] = "N/A"
            row[f"{side}_RSI_asof_UTC"] = ""
            row[f"{side}_RSI_source"] = "openchart"
            row[f"{side}_RSI_symbol"] = ""
    client = None
    try:
        client = client_factory()
        add_option_rsi(client, fallback, expiry)
    except Exception as exc:
        print(f"OpenChart RSI failed: {exc}. Unavailable values remain N/A.", file=sys.stderr)
    finally:
        if client is not None:
            client.session.close()
    for original, updated in zip(pending, fallback):
        for side in ("CE", "PE"):
            if source == "openchart" or original.get(f"{side}_RSI14_15m", "N/A") == "N/A":
                for suffix in ("RSI14_15m", "RSI_asof_UTC", "RSI_source", "RSI_symbol"):
                    original[f"{side}_{suffix}"] = updated[f"{side}_{suffix}"]



def add_tradingview_rsi(rows):
    """Fetch NIFTY index RSI once; never label it as individual option RSI.

    Scanner RSI can include the currently forming 15-minute candle. Retrieval
    time is recorded separately; TradingView does not expose a bar time here.
    """
    value = "N/A"
    retrieved = ""
    for row in rows:
        for side in ("CE", "PE"):
            row[f"{side}_RSI14_15m"] = "N/A"
            row[f"{side}_RSI_asof_UTC"] = ""
            row[f"{side}_RSI_source"] = "unavailable (index RSI only)"
            row[f"{side}_RSI_symbol"] = ""
    try:
        from tradingview_ta import TA_Handler, Interval
        handler = TA_Handler(symbol="NIFTY", exchange="NSE", screener="india",
                             interval=Interval.INTERVAL_15_MINUTES, timeout=15)
        analysis = handler.get_analysis()
        raw = analysis.indicators.get("RSI")
        if raw is None or isinstance(raw, bool):
            raise ValueError("TradingView returned no RSI")
        number = float(raw)
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError("TradingView returned an invalid RSI")
        value = round(number, 2)
        retrieved = datetime.now(timezone.utc).isoformat()
    except ImportError:
        print("TradingView RSI unavailable: run python3 -m pip install -r requirements.txt", file=sys.stderr)
    except Exception as exc:
        print(f"TradingView NIFTY RSI unavailable: {exc}", file=sys.stderr)
    for row in rows:
        row["NIFTY_index_RSI14_15m"] = value
        row["NIFTY_index_RSI_source"] = "tradingview-ta"
        row["NIFTY_index_RSI_symbol"] = "NSE:NIFTY"
        row["NIFTY_index_RSI_retrieved_UTC"] = retrieved
    return value


def tradingview_option_symbol(expiry, strike, side):
    """TradingView NSE option ticker: NIFTY + YYMMDD + C/P + strike.

    Format reference:
    https://www.tradingview.com/support/solutions/43000480528/
    """
    from decimal import Decimal

    if side not in ("CE", "PE"):
        raise ValueError("Option side must be CE or PE")
    price = Decimal(str(strike))
    if not price.is_finite() or price <= 0:
        raise ValueError("Strike must be a positive finite number")
    date = datetime.strptime(expiry, "%d-%b-%Y").strftime("%y%m%d")
    return f"NSE:NIFTY{date}{side[0]}{format(price.normalize(), 'f')}"


def add_tradingview_option_rsi(rows, expiry):
    """Fetch each option's pre-calculated RSI in one scanner request.

    Chart availability does not guarantee scanner coverage. Absent contracts or
    invalid RSI values retain N/A; index RSI is never copied to option rows.
    Scanner RSI may include the forming candle; retrieval time is not bar time.
    """
    symbols = []
    for row in rows:
        for side in ("CE", "PE"):
            symbol = tradingview_option_symbol(expiry, row["strikePrice"], side)
            symbols.append(symbol)
            row[f"{side}_RSI14_15m"] = "N/A"
            row[f"{side}_RSI_asof_UTC"] = ""
            row[f"{side}_RSI_source"] = "tradingview-ta"
            row[f"{side}_RSI_symbol"] = symbol
            row[f"{side}_RSI_retrieved_UTC"] = ""
            row[f"{side}_RSI_status"] = "unavailable"
    if not symbols:
        return
    try:
        from tradingview_ta import get_multiple_analysis, Interval
        analyses = get_multiple_analysis(
            screener="india", interval=Interval.INTERVAL_15_MINUTES,
            symbols=list(dict.fromkeys(symbols)), timeout=15,
        )
        if not isinstance(analyses, dict):
            raise ValueError("TradingView returned an unexpected scanner response")
    except Exception as exc:
        print(f"TradingView option RSI request failed: {exc}. Option RSI remains N/A.", file=sys.stderr)
        return
    retrieved = datetime.now(timezone.utc).isoformat()
    available = 0
    for row in rows:
        for side in ("CE", "PE"):
            analysis = analyses.get(row[f"{side}_RSI_symbol"])
            row[f"{side}_RSI_retrieved_UTC"] = retrieved
            if analysis is None:
                row[f"{side}_RSI_status"] = "not returned by scanner"
                continue
            try:
                raw = analysis.indicators.get("RSI")
                if raw is None or isinstance(raw, bool):
                    raise ValueError("RSI missing")
                number = float(raw)
                if not math.isfinite(number) or not 0 <= number <= 100:
                    raise ValueError("RSI invalid")
            except (AttributeError, TypeError, ValueError):
                row[f"{side}_RSI_status"] = "missing or invalid RSI"
                continue
            row[f"{side}_RSI14_15m"] = round(number, 2)
            row[f"{side}_RSI_status"] = "available"
            available += 1
    print(f"TradingView option RSI(14, 15m): {available}/{len(symbols)} call/put contracts available.", file=sys.stderr)
    if available < len(symbols):
        print("N/A means the exact option contract or RSI was not returned by TradingView's scanner.", file=sys.stderr)
