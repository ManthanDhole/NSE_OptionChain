"""Show NIFTY spot price and option strikes within +/-600 points; save CSV.

Usage:
    python3 nifty_option_chain.py
    python3 nifty_option_chain.py --list-expiries
    python3 nifty_option_chain.py --expiry 10-Nov-2026 --output nifty.csv

Requires Python 3.10+. NSE website endpoints may change or block automated requests.
"""

import argparse
import csv
import math
import os
import re
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo



def flatten_chain(payload, expiry):
    records = payload.get("records")
    if not isinstance(records, dict) or not isinstance(records.get("data"), list):
        raise ValueError("NSE response is missing records.data; no CSV was written.")
    fields = (
        "lastPrice", "change", "pChange", "openInterest", "changeinOpenInterest",
        "pchangeinOpenInterest", "totalTradedVolume", "impliedVolatility",
        "bidQty", "bidprice", "askPrice", "askQty",
    )
    rows = []
    for entry in records["data"]:
        row_expiry = entry.get("expiryDate") or entry.get("expiryDates")
        if row_expiry and row_expiry != expiry:
            continue
        ce, pe = entry.get("CE") or {}, entry.get("PE") or {}
        if not ce and not pe:
            continue
        row = {
            "timestamp": records.get("timestamp", ""),
            "underlyingValue": records.get("underlyingValue", ""),
            "expiryDate": row_expiry or expiry,
            "strikePrice": entry["strikePrice"],
        }
        for side, leg in (("CE", ce), ("PE", pe)):
            for field in fields:
                row[f"{side}_{field}"] = leg.get(field, "")
        rows.append(row)
    if not rows:
        raise ValueError(f"NSE returned no option-chain rows for {expiry}; no CSV was written.")
    return sorted(rows, key=lambda row: float(row["strikePrice"]))



def select_strike_range(payload, rows):
    """Select strikes within 600 points of the snapshot spot price, inclusive."""
    records = payload["records"]
    candidates = [records.get("underlyingValue")]
    # Some responses expose the spot price only inside individual option legs.
    for entry in records["data"]:
        for side in ("CE", "PE"):
            candidates.append((entry.get(side) or {}).get("underlyingValue"))
    spot = None
    for value in candidates:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            spot = price
            break
    if spot is None:
        raise ValueError("NSE returned no valid NIFTY spot price; cannot select strike range.")
    lower, upper = spot - 600, spot + 600
    selected = [dict(row, underlyingValue=spot) for row in rows
                if lower <= float(row["strikePrice"]) <= upper]
    return spot, lower, upper, selected



def print_option_table(rows, spot, index_rsi=None):
    """Insert a spot-price marker between lower and higher strikes."""
    print(f"{'CALL':^50} | {'STRIKE':^12} | {'PUT':^50}")
    print(f"{'RSI14/15m':>9} {'LTP':>12} {'OI':>12} {'Volume':>14} | {'Price':^12} | {'Volume':>14} {'OI':>12} {'LTP':>12} {'RSI14/15m':>9}")
    print('-' * 118)

    def print_spot():
        print('=' * 118)
        print(f"{'NIFTY CURRENT PRICE':>50} | {spot:^12,.2f} | {'':50}")
        if index_rsi is not None:
            print(f"{'NIFTY INDEX RSI (15m, TradingView)':>50} | {index_rsi:^12} | {'':50}")
        print('=' * 118)

    spot_shown = False
    for row in sorted(rows, key=lambda item: float(item['strikePrice'])):
        if not spot_shown and float(row['strikePrice']) >= spot:
            print_spot()
            spot_shown = True
        print(
            f"{row.get('CE_RSI14_15m', 'N/A'):>9} {row['CE_lastPrice']:>12} {row['CE_openInterest']:>12} {row['CE_totalTradedVolume']:>14}"
            f" | {row['strikePrice']:^12} | "
            f"{row['PE_totalTradedVolume']:>14} {row['PE_openInterest']:>12} {row['PE_lastPrice']:>12} {row.get('PE_RSI14_15m', 'N/A'):>9}"
        )
    if not spot_shown:
        print_spot()



def cleanup_generated_csvs(directory, keep=3):
    """Keep newest timestamped snapshots; leave unrelated CSVs and symlinks alone."""
    pattern = re.compile(r"nifty_option_chain_\d{8}_\d{6}_\d{6}\.csv")
    removed = 0
    try:
        snapshots = sorted(
            (path for path in directory.iterdir()
             if pattern.fullmatch(path.name) and not path.is_symlink() and path.is_file()),
            key=lambda path: path.name,
            reverse=True,
        )
    except OSError as exc:
        print(f"CSV cleanup skipped: {exc}", file=sys.stderr)
        return removed
    for path in snapshots[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            print(f"Could not remove old snapshot {path.name}: {exc}", file=sys.stderr)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expiry", help="Expiry from --list-expiries, e.g. 10-Nov-2026")
    parser.add_argument("--list-expiries", action="store_true", help="List available expiry dates and exit")
    parser.add_argument("--output", type=Path, help="CSV destination; defaults to a timestamped file beside this script")
    parser.add_argument("--rsi-source", choices=("tradingview", "auto", "yfinance", "openchart"), default="tradingview",
                        help="Default: tradingview fetches index and per-option RSI; auto/yfinance/openchart fetch per-option RSI")
    args = parser.parse_args()

    # Install system certificate handling before importing Requests/urllib3.
    try:
        import truststore
        truststore.inject_into_ssl()
        import requests
        from openchart_option_chain import NSEOptionChain
        from openchart import NSEData
        from option_rsi import add_rsi, add_tradingview_rsi, add_tradingview_option_rsi
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    try:
        nse = NSEOptionChain()
        try:
            nse.connect()
            expiries = nse.available_expiries("NIFTY")
            if args.list_expiries:
                print("Available NIFTY expiries:")
                print("\n".join(expiries))
                return 0
            expiry = args.expiry or expiries[0]
            if expiry not in expiries:
                raise ValueError(f"Unavailable expiry {expiry!r}. Available: {', '.join(expiries)}")
            payload = nse.option_chain(expiry, "NIFTY")
        finally:
            nse.session.close()
        rows = flatten_chain(payload, expiry)
        spot, lower, upper, rows = select_strike_range(payload, rows)
        print(f"NIFTY current market price: {spot:,.2f}")
        print(f"NSE snapshot timestamp: {payload['records'].get('timestamp', 'Unavailable')}")
        print(f"Expiry: {expiry} | Strike range: {lower:,.2f} to {upper:,.2f} (inclusive)")
        if not rows:
            raise ValueError("No strikes available within 600 points of NIFTY spot; no CSV was written.")
        print(f"RSI provider: {args.rsi_source}", flush=True)
        index_rsi = None
        if args.rsi_source == "tradingview":
            index_rsi = add_tradingview_rsi(rows)
            print(f"NIFTY index RSI (15m): {index_rsi}")
            print("Loading TradingView RSI for each call and put contract...", flush=True)
            add_tradingview_option_rsi(rows, expiry)
            print("TradingView RSI may include the current 15-minute candle.")
        else:
            print("Loading RSI(14) from completed 15-minute option candles...", flush=True)
            add_rsi(rows, expiry, args.rsi_source, NSEData)
        stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S_%f")
        snapshot_dir = Path(os.environ.get("NIFTY_DATA_DIR", Path(__file__).resolve().parent))
        if args.output is None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
        output = args.output or snapshot_dir / f"nifty_option_chain_{stamp}.csv"
        # Avoid silently replacing an existing snapshot.
        with output.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        # Prune only after the new snapshot has been completely written and closed.
        removed = cleanup_generated_csvs(output.resolve().parent)
        if removed:
            print(f"Removed {removed} older CSV snapshots; kept the newest 3.")
        print(f"Strikes: {len(rows)}")
        print(f"Saved: {output.resolve()}")
        print_option_table(rows, spot, index_rsi)
        return 0
    except requests.exceptions.SSLError as exc:
        print(f"Certificate verification failed: {exc}. Trust your company CA in the OS certificate store, or set REQUESTS_CA_BUNDLE to an approved PEM bundle (needed inside Docker on inspected networks).", file=sys.stderr)
    except requests.exceptions.HTTPError as exc:
        print(f"NSE request failed: {exc}. NSE may be blocking automated access; try again later.", file=sys.stderr)
    except requests.exceptions.RequestException as exc:
        print(f"Could not connect to NSE: {exc}", file=sys.stderr)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
