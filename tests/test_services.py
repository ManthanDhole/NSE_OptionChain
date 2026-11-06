import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from services.schedule import market_gate
from services.store import numeric


class ScheduleTests(unittest.TestCase):
    def gate(self, clock, holidays=(), year=2026):
        return market_gate(datetime.fromisoformat(clock), holidays, year)[0]

    def test_open_is_ist_and_inclusive(self):
        self.assertTrue(self.gate('2026-11-06T09:15:00+05:30'))
        self.assertTrue(self.gate('2026-11-06T03:45:00+00:00'))

    def test_close_is_exclusive(self):
        self.assertTrue(self.gate('2026-11-06T15:29:59+05:30'))
        self.assertFalse(self.gate('2026-11-06T15:30:00+05:30'))
        self.assertFalse(self.gate('2026-11-06T09:14:59+05:30'))

    def test_weekends_holidays_and_unknown_year_are_closed(self):
        self.assertFalse(self.gate('2026-11-07T10:00:00+05:30'))
        self.assertFalse(self.gate('2026-11-08T10:00:00+05:30'))
        self.assertFalse(self.gate('2026-11-06T10:00:00+05:30', ['2026-11-06']))
        self.assertFalse(self.gate('2026-11-06T10:00:00+05:30', None, None))
        self.assertFalse(self.gate('2027-01-01T10:00:00+05:30'))

    def test_numeric_unavailable_and_zero(self):
        self.assertIsNone(numeric('N/A'))
        self.assertIsNone(numeric(float('nan')))
        self.assertEqual(numeric(0), Decimal(0))


@unittest.skipUnless(os.getenv('RUN_DATABASE_TESTS') == '1', 'Run scripts/test_database.py for isolated PostgreSQL checks')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        from services.store import connect
        self.connect = connect
        from psycopg.conninfo import make_conninfo
        import psycopg
        self.admin = psycopg.connect(os.environ['ADMIN_DATABASE_URL'], autocommit=True)
        self.admin.execute('TRUNCATE snapshots,collection_attempts CASCADE')
        self.admin.execute("UPDATE collector_state SET payload='{}',heartbeat=now()")
        self.addCleanup(self.admin.close)

    def snapshot(self):
        rows = []
        for strike in (23000, 24000, 24600):
            rows.append({'strikePrice': strike, 'expiryDate': '09-Nov-2026', 'underlyingValue': 24000,
                         'CE_lastPrice': 100, 'CE_openInterest': 1000, 'CE_totalTradedVolume': 2000,
                         'PE_lastPrice': 90, 'PE_openInterest': 1200, 'PE_totalTradedVolume': 500,
                         'CE_RSI14_15m': 'N/A'})
        return {'id': str(uuid.uuid4()), 'collected_at': datetime.now(timezone.utc), 'expiry': '09-Nov-2026',
                'spot': 24000, 'index_rsi': 55.5, 'market_timestamp': '06-Nov-2026 10:00:00',
                'raw_payload': {'records': {'data': rows}}, 'rows': rows, 'warnings': ['test fixture']}

    def test_roundtrip_all_strikes_stored_ui_filters_only_at_read(self):
        from services.store import save_snapshot, get_snapshot
        data = self.snapshot()
        key = save_snapshot(data)
        self.assertEqual(len(get_snapshot(key, full=True)['rows']), 3)
        self.assertEqual(len(get_snapshot(key)['rows']), 2)
        self.assertEqual(self.admin.execute('SELECT count(*) FROM option_quotes').fetchone()[0], 6)
        self.assertEqual(self.admin.execute('SELECT raw_payload FROM snapshots').fetchone()[0], data['raw_payload'])

    def test_retry_is_idempotent(self):
        from services.store import save_snapshot
        data = self.snapshot()
        save_snapshot(data)
        save_snapshot(data)
        self.assertEqual(self.admin.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)
        self.assertEqual(self.admin.execute('SELECT count(*) FROM option_quotes').fetchone()[0], 6)

    def test_failed_insert_rolls_back_complete_snapshot(self):
        from services.store import save_snapshot
        import psycopg
        data = self.snapshot()
        data['rows'][0]['strikePrice'] = None
        with self.assertRaises(psycopg.Error):
            save_snapshot(data)
        self.assertEqual(self.admin.execute('SELECT count(*) FROM snapshots').fetchone()[0], 0)

    def test_database_failure_preserves_spool_and_retry_commits(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from services.collector import queue_snapshot, drain_spool
        with self.connect() as conn:
            attempt = conn.execute('INSERT INTO collection_attempts(started_at) VALUES(now()) RETURNING id').fetchone()['id']
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'SPOOL_DIR': folder}):
            path = queue_snapshot(self.snapshot(), attempt)
            with patch('services.collector.save_snapshot', side_effect=RuntimeError('database offline')):
                with self.assertRaises(RuntimeError):
                    drain_spool()
            self.assertTrue(path.exists())
            drain_spool()
            self.assertFalse(path.exists())
            self.assertEqual(self.admin.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)

    def test_api_role_cannot_write_market_data(self):
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.connect(True) as conn:
                conn.execute('DELETE FROM snapshots')
        with self.connect(True) as conn:
            conn.execute('INSERT INTO refresh_requests DEFAULT VALUES')

    def test_collector_cannot_delete_history(self):
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.connect() as conn:
                conn.execute('DELETE FROM snapshots')

    def test_api_reads_committed_snapshot_and_rejects_closed_session_refresh(self):
        import json
        import threading
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError
        from http.server import ThreadingHTTPServer
        from services.api import Handler
        from services.store import save_snapshot, state_update
        save_snapshot(self.snapshot())
        state_update(market_open=False, loading=False, schedule_reason='Outside market hours')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url+'/api/state') as response:
                state = json.load(response)
            self.assertEqual(state['snapshot']['spot'], 24000)
            with urlopen(url+'/api/download?id='+state['snapshot']['id']) as response:
                self.assertIn(b'23000', response.read())
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url+'/api/refresh', method='POST'))
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
            with urlopen(url+'/api/snapshots/'+state['snapshot']['id']+'/raw') as response:
                self.assertIn('records', json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
