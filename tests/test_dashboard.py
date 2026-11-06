import json
from pathlib import Path
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import Dashboard, ROOT, REFRESH_SECONDS, handler_for
from http.server import ThreadingHTTPServer


SNAPSHOT = {"rows": [{"strikePrice": "24500"}], "spot": 24536.6,
            "filename": "nifty_option_chain_20261105_230540_319715.csv"}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = Dashboard()
        self.app.state.update(snapshot=dict(SNAPSHOT), cached=True)

    def test_success_uses_script_and_replaces_cached_snapshot(self):
        path = self.app.data_dir / SNAPSHOT["filename"]
        result = subprocess.CompletedProcess([], 0, f"Saved: {path}\n", "RSI unavailable\n")
        with patch("dashboard.subprocess.run", return_value=result) as run, patch("dashboard.read_snapshot", return_value=SNAPSHOT):
            self.app.refresh()
        state = self.app.get_state()
        self.assertFalse(state["cached"])
        self.assertFalse(state["loading"])
        self.assertIsNotNone(state["last_success"])
        self.assertEqual(state["warnings"], ["RSI unavailable"])
        self.assertIn("--rsi-source", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 110)

    def test_configured_data_folder_is_created_and_passed_to_script(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary) / "snapshots"
            with patch("dashboard.SNAPSHOT_DIR", folder):
                app = Dashboard()
            self.assertTrue(folder.is_dir())
            result = subprocess.CompletedProcess([], 1, "", "offline")
            with patch("dashboard.subprocess.run", return_value=result) as run:
                app.refresh()
            self.assertEqual(run.call_args.kwargs["env"]["NIFTY_DATA_DIR"], str(folder))

    def test_download_reads_configured_data_folder(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            name = SNAPSHOT["filename"]
            content = "strikePrice,underlyingValue,expiryDate\n24500,24536.6,09-Nov-2026\n"
            (folder / name).write_text(content)
            with patch("dashboard.SNAPSHOT_DIR", folder):
                app = Dashboard()
            self.assertTrue(app.state["cached"])
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/download") as response:
                    self.assertEqual(response.read().decode(), content)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)

    def test_failed_fetch_preserves_previous_data(self):
        with patch("dashboard.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "NSE offline")):
            self.app.refresh()
        self.assertEqual(self.app.state["snapshot"], SNAPSHOT)
        self.assertIn("NSE offline", self.app.state["error"])
        self.assertFalse(self.app.state["loading"])

    def test_timeout_preserves_previous_data(self):
        with patch("dashboard.subprocess.run", side_effect=subprocess.TimeoutExpired("script", 110)):
            self.app.refresh()
        self.assertEqual(self.app.state["snapshot"], SNAPSHOT)
        self.assertIn("110 seconds", self.app.state["error"])

    def test_loading_is_visible_and_manual_requests_do_not_overlap(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(2)
            return subprocess.CompletedProcess([], 1, "", "offline")
        with patch("dashboard.subprocess.run", side_effect=blocked):
            worker = threading.Thread(target=self.app.refresh)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.app.get_state()["loading"])
                self.assertFalse(self.app.request_refresh())
            finally:
                release.set()
                worker.join(2)

    def test_schedule_targets_five_seconds_between_fetch_starts(self):
        def end_after_one(delay):
            self.assertEqual(delay, 2)
            self.app.stop.set()
        self.app.refresh = Mock()
        self.app.wake.wait = Mock(side_effect=end_after_one)
        with patch("dashboard.time.monotonic", side_effect=[100, 103]):
            self.app.run()
        self.app.refresh.assert_called_once()
        self.assertEqual(REFRESH_SECONDS, 5)

    def test_unexpected_output_path_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, "Saved: /tmp/unrelated.csv\n", "")
        with patch("dashboard.subprocess.run", return_value=result):
            self.app.refresh()
        self.assertIn("unexpected snapshot path", self.app.state["error"])

    def test_http_state_page_and_manual_refresh(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(url + "/api/state") as response:
                self.assertEqual(json.load(response)["snapshot"]["spot"], 24536.6)
            with urlopen(url) as response:
                self.assertIn(b"CALL OPTIONS", response.read())
            with urlopen(Request(url + "/api/refresh", method="POST")) as response:
                self.assertTrue(json.load(response)["accepted"])
            self.assertTrue(self.app.wake.is_set())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)


if __name__ == "__main__":
    unittest.main()
