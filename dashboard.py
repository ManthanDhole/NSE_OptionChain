"""Local NIFTY dashboard. Run: python3 dashboard.py (then open localhost:8765)."""

import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATTERN = re.compile(r"nifty_option_chain_\d{8}_\d{6}_\d{6}\.csv")
REFRESH_SECONDS = 5
SNAPSHOT_DIR = Path(os.environ.get("NIFTY_DATA_DIR", ROOT)).resolve()


def read_snapshot(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(key not in rows[0] for key in ("strikePrice", "underlyingValue", "expiryDate")):
        raise ValueError("CSV snapshot has no usable option-chain rows")
    spot = float(rows[0]["underlyingValue"])
    rows.sort(key=lambda row: float(row["strikePrice"]))
    return {
        "rows": rows, "spot": spot, "expiry": rows[0]["expiryDate"],
        "market_timestamp": rows[0].get("timestamp", ""),
        "index_rsi": rows[0].get("NIFTY_index_RSI14_15m", "N/A"),
        "filename": path.name,
    }


class Dashboard:
    def __init__(self, python=sys.executable, expiry=None):
        self.python, self.expiry = python, expiry
        self.data_dir = SNAPSHOT_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.state = {"snapshot": None, "loading": False, "error": None,
                      "warnings": [], "last_success": None, "next_refresh": None,
                      "cached": False, "refresh_seconds": REFRESH_SECONDS}
        # Show the most recent saved snapshot immediately, clearly marked as cached.
        candidates = sorted((p for p in self.data_dir.iterdir() if SNAPSHOT_PATTERN.fullmatch(p.name)
                             and p.is_file() and not p.is_symlink()), reverse=True)
        for path in candidates:
            try:
                snapshot = read_snapshot(path)
                if expiry and snapshot["expiry"] != expiry:
                    continue
                self.state.update(snapshot=snapshot, cached=True)
                break
            except (OSError, ValueError, KeyError):
                continue

    def get_state(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def refresh(self):
        with self.lock:
            self.state.update(loading=True, error=None, next_refresh=None)
        try:
            command = [self.python, str(ROOT / "nifty_option_chain.py"), "--rsi-source", "tradingview"]
            if self.expiry:
                command.extend(["--expiry", self.expiry])
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=110,
                                    env={**os.environ, "NIFTY_DATA_DIR": str(self.data_dir)})
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(detail[-1500:] or "The data script exited without a snapshot.")
            saved = [line[7:].strip() for line in result.stdout.splitlines() if line.startswith("Saved: ")]
            if not saved:
                raise ValueError("The data script did not report a saved snapshot.")
            path = Path(saved[-1]).resolve()
            if path.parent != self.data_dir or not SNAPSHOT_PATTERN.fullmatch(path.name):
                raise ValueError("The data script reported an unexpected snapshot path.")
            snapshot = read_snapshot(path)
            warnings = [line.strip() for line in result.stderr.splitlines() if line.strip()]
            with self.lock:
                self.state.update(snapshot=snapshot, last_success=datetime.now(timezone.utc).isoformat(),
                                  warnings=warnings[-5:], cached=False)
        except subprocess.TimeoutExpired:
            with self.lock:
                self.state["error"] = "Fetching exceeded 110 seconds. Showing the last saved data; another refresh will be attempted."
        except (OSError, ValueError, RuntimeError) as exc:
            with self.lock:
                self.state["error"] = str(exc)
        finally:
            with self.lock:
                self.state["loading"] = False

    def run(self):
        while not self.stop.is_set():
            # Consume refresh requests before starting, so clicks during a fetch do
            # not queue a second request. The single worker guarantees no overlap.
            self.wake.clear()
            started = time.monotonic()
            self.refresh()
            delay = max(0, REFRESH_SECONDS - (time.monotonic() - started))
            with self.lock:
                self.state["next_refresh"] = time.time() + delay
            self.wake.wait(delay)

    def request_refresh(self):
        with self.lock:
            if self.state["loading"]:
                return False
            self.wake.set()
            return True


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/api/state":
                self.send(200, json.dumps(app.get_state()).encode())
            elif route == "/":
                self.send(200, (ROOT / "ui" / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif route == "/api/download":
                state = app.get_state()
                name = (state.get("snapshot") or {}).get("filename", "")
                if not SNAPSHOT_PATTERN.fullmatch(name):
                    self.send(404, b'{"error":"No snapshot available"}')
                    return
                try:
                    self.send(200, (app.data_dir / name).read_bytes(), "text/csv; charset=utf-8")
                except OSError:
                    self.send(404, b'{"error":"Snapshot was rotated; refresh and try again"}')
            else:
                self.send(404, b'{"error":"Not found"}')

        def do_POST(self):
            # Allow same-origin browser requests only; this app is local.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send(403, b'{"error":"Origin not allowed"}')
            elif self.path == "/api/refresh":
                accepted = app.request_refresh()
                self.send(202, json.dumps({"accepted": accepted}).encode())
            else:
                self.send(404, b'{"error":"Not found"}')

        def log_message(self, format, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; use 0.0.0.0 inside Docker")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--expiry", help="Optional expiry in DD-Mon-YYYY format; default: nearest available")
    parser.add_argument("--python", default=sys.executable, help="Python executable with requirements.txt installed")
    args = parser.parse_args()
    app = Dashboard(args.python, args.expiry)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(app))
    worker = threading.Thread(target=app.run, daemon=True)
    worker.start()
    def stop_on_signal(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_signal)
    print(f"NIFTY dashboard: http://localhost:{args.port} | Refresh: 5 seconds", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        app.wake.set()
        server.server_close()


if __name__ == "__main__":
    main()
