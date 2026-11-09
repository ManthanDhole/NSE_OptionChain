import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from services.store import numeric


class ContinuousCollectionTests(unittest.TestCase):
    def test_fetches_before_open_after_close_weekends_and_holidays(self):
        from unittest.mock import MagicMock, patch
        import threading
        import types
        import sys
        from services import collector
        for instant in ('2026-11-06T08:00:00+05:30', '2026-11-06T20:00:00+05:30',
                        '2026-11-07T10:00:00+05:30', '2026-01-26T10:00:00+05:30'):
            with self.subTest(instant=instant):
                captured = datetime.fromisoformat(instant)
                class FrozenDateTime(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return captured.astimezone(tz) if tz else captured
                stopped = threading.Event()
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.execute.return_value.fetchone.side_effect = [{'acquired': True}, {'id': 1}]
                connection.execute.return_value.fetchall.return_value = []
                snapshot = {'rows': [], 'collected_at': captured}
                def fetch():
                    stopped.set()
                    return snapshot
                provider = types.ModuleType('services.provider')
                provider.fetch_snapshot = MagicMock(side_effect=fetch)
                with patch.object(collector, 'STOP', stopped), patch.object(collector, 'connect', return_value=connection), \
                     patch.object(collector, 'datetime', FrozenDateTime), patch.object(collector, 'drain_spool'), \
                     patch.object(collector, 'queue_snapshot') as queued, patch.object(collector, 'state_update') as status, \
                     patch.dict(sys.modules, {'services.provider': provider}):
                    collector.run()
                provider.fetch_snapshot.assert_called_once()
                queued.assert_called_once_with(snapshot, 1)
                self.assertTrue(any(call.kwargs.get('collector_available') is True for call in status.call_args_list))

    def test_close_summary_groups_ties_and_excludes_missing_values(self):
        from services.store import summarize_close
        snapshot = dict(id='test', expiry='12-Nov-2026', spot=25000,
                        market_timestamp='06-Nov-2026 15:40:00', collected_at='test', rows=[
            {'strikePrice': 24900, 'CE_totalTradedVolume': 100, 'CE_openInterest': 20},
            {'strikePrice': 25000, 'CE_totalTradedVolume': 100, 'CE_openInterest': 10},
            {'strikePrice': 25100, 'CE_totalTradedVolume': 75, 'CE_openInterest': 0},
            {'strikePrice': 25200, 'CE_totalTradedVolume': 'N/A', 'CE_openInterest': -1}])
        result = summarize_close(snapshot)
        volume = result['sides']['CE']['volume']
        self.assertEqual(volume['total'], 275)
        self.assertEqual(volume['top'][0]['strikes'], [24900, 25000])
        self.assertEqual(volume['top'][1]['value'], 75)
        self.assertEqual(volume['available_strikes'], 3)
        self.assertIsNone(result['sides']['PE']['volume']['total'])
        self.assertIsNone(result['combined']['volume'])

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
        totals = get_snapshot(key)['totals']
        self.assertEqual(totals['strike_count'], 3)
        self.assertEqual(totals['CE_totalTradedVolume'], 6000)
        self.assertEqual(totals['CE_openInterest'], 3000)
        self.assertEqual(totals['PE_totalTradedVolume'], 1500)
        self.assertEqual(totals['PE_openInterest'], 3600)

        self.assertEqual(self.admin.execute('SELECT count(*) FROM option_quotes').fetchone()[0], 6)
        self.assertEqual(self.admin.execute('SELECT raw_payload FROM snapshots').fetchone()[0], data['raw_payload'])

    def test_bidirectional_strength_transition_time_is_stable_and_boundary_is_neutral(self):
        from services.store import save_snapshot, get_strength_transitions
        def capture(clock, second, day='2026-11-09'):
            data = self.snapshot()
            data['collected_at'] = datetime.fromisoformat(day+'T'+clock+'+05:30')
            for row in data['rows']:
                row['CE_totalTradedVolume'] = 100 if row['strikePrice']==24000 else second
                row['PE_totalTradedVolume'] = 100 if row['strikePrice']==24600 else second
            return save_snapshot(data)
        weak = capture('10:00:00', 80)
        self.assertEqual(get_strength_transitions(weak), {})
        neutral = capture('10:00:05', 75)
        self.assertEqual(get_strength_transitions(neutral), {})
        strong = capture('10:00:10', 74)
        event = get_strength_transitions(strong)
        self.assertEqual(event['CE']['from_status'], 'WTT')
        self.assertEqual(event['PE']['from_status'], 'WTB')
        self.assertEqual(event['CE']['to_status'], 'STRONG')
        self.assertEqual(datetime.fromisoformat(event['CE']['at']), datetime.fromisoformat('2026-11-09T10:00:10+05:30'))
        later = capture('10:00:15', 60)
        self.assertEqual(get_strength_transitions(later), event)
        get_strength_transitions.cache_clear()
        self.assertEqual(get_strength_transitions(later), event)
        weak_again = capture('10:00:20', 80)
        reverse = get_strength_transitions(weak_again)
        self.assertEqual(reverse['CE']['from_status'], 'STRONG')
        self.assertEqual(reverse['CE']['to_status'], 'WTT')
        self.assertEqual(reverse['PE']['from_status'], 'STRONG')
        self.assertEqual(reverse['PE']['to_status'], 'WTB')
        self.assertEqual(datetime.fromisoformat(reverse['CE']['at']), datetime.fromisoformat('2026-11-09T10:00:20+05:30'))
        weak_later = capture('10:00:22', 85)
        self.assertEqual(get_strength_transitions(weak_later), reverse)
        get_strength_transitions.cache_clear()
        self.assertEqual(get_strength_transitions(weak_later), reverse)
        strong_again = capture('10:00:25', 70)
        self.assertNotEqual(get_strength_transitions(strong_again)['CE']['at'], event['CE']['at'])
        tomorrow = capture('10:00:00', 70, '2026-11-10')
        self.assertEqual(get_strength_transitions(tomorrow), {})

    def test_volume_shifts_are_per_side_persist_and_reset(self):
        from services.store import save_snapshot, get_volume_shifts
        def capture(clock, call, put, day='2026-11-09', expiry='09-Nov-2026'):
            data = self.snapshot()
            data.update(collected_at=datetime.fromisoformat(day+'T'+clock+'+05:30'), expiry=expiry)
            for row in data['rows']:
                row['expiryDate'] = expiry
                row['CE_totalTradedVolume'] = 100 if row['strikePrice'] in call else 50
                row['PE_totalTradedVolume'] = 100 if row['strikePrice'] in put else 50
                if row['strikePrice'] == 23000:  # Outside displayed range: must not influence leader.
                    row['CE_totalTradedVolume'] = 10000
            return save_snapshot(data)
        first = capture('09:30:00', [24000], [24600])
        self.assertEqual(get_volume_shifts(first), {})
        second = capture('09:30:05', [24600], [24000])
        shift = get_volume_shifts(second)
        self.assertEqual(shift['CE']['direction'], 'SHIFTED FROM BOTTOM TO TOP')
        self.assertEqual(shift['CE']['from_strikes'], [24000])
        self.assertEqual(shift['CE']['to_strikes'], [24600])
        self.assertEqual(shift['PE']['direction'], 'SHIFTED FROM TOP TO BOTTOM')
        third = capture('09:30:10', [24600], [24000])
        self.assertEqual(get_volume_shifts(third), shift)
        get_volume_shifts.cache_clear()  # Reconstruct after restart, without browser memory.
        self.assertEqual(get_volume_shifts(third), shift)
        tied = capture('09:30:15', [24000, 24600], [24000])
        self.assertEqual(get_volume_shifts(tied)['CE']['direction'], 'VOLUME LEADER TIE CHANGED')
        next_day = capture('09:30:00', [24000], [24600], day='2026-11-10')
        self.assertEqual(get_volume_shifts(next_day), {})
        new_expiry = capture('09:31:00', [24600], [24000], expiry='16-Nov-2026')
        self.assertEqual(get_volume_shifts(new_expiry), {})

    def test_previous_close_uses_exchange_day_and_all_strikes(self):
        from datetime import date
        from services.store import save_snapshot, get_previous_close
        today = date(2026, 11, 9)  # Monday: use Friday's stored close, not Sunday capture time.
        self.assertIsNone(get_previous_close(today)['summary'])
        def store(market):
            data = self.snapshot()
            data.update(collected_at=datetime.fromisoformat('2026-11-08T19:00:00+05:30'), market_timestamp=market)
            return save_snapshot(data)
        store('06-Nov-2026 15:29:59')
        self.assertIsNone(get_previous_close(today)['summary'])
        store('05-Nov-2026 15:40:00')
        key = store('06-Nov-2026 15:40:00')
        store('09-Nov-2026 15:40:00')  # Today's data is never the previous day.
        result = get_previous_close(today)['summary']
        self.assertEqual(result['session_day'], '2026-11-06')
        self.assertEqual(result['snapshot']['id'], key)
        self.assertEqual(result['strike_count'], 3)
        self.assertEqual(result['sides']['CE']['volume']['total'], 6000)
        self.assertEqual(result['sides']['PE']['oi']['total'], 3600)
        self.assertEqual(result['combined']['volume'], 7500)
        self.assertEqual(result['combined']['oi'], 6600)
        self.assertEqual(result['sides']['CE']['volume']['top'][0]['strikes'], [23000, 24000, 24600])

    def test_920_reference_uses_first_fresh_capture_in_ist_minute(self):
        from datetime import date
        from services.store import save_snapshot, get_920_baseline
        day = date(2026, 11, 9)
        self.assertIsNone(get_920_baseline(day)['snapshot'])
        for captured, market in (
            ('09:19:59', '09-Nov-2026 09:19:59'),
            ('09:20:00', '06-Nov-2026 09:20:00'),
            ('09:20:01', '09-Nov-2026 09:19:59'),
            ('09:21:00', '09-Nov-2026 09:21:00'),
        ):
            data = self.snapshot()
            data.update(collected_at=datetime.fromisoformat('2026-11-09T'+captured+'+05:30'), market_timestamp=market)
            save_snapshot(data)
        self.assertIsNone(get_920_baseline(day)['snapshot'])
        data = self.snapshot()
        data.update(collected_at=datetime.fromisoformat('2026-11-09T09:20:02+05:30'), market_timestamp='09-Nov-2026 09:20:01')
        first_id = save_snapshot(data)
        data['id'] = str(uuid.uuid4())
        data['collected_at'] = datetime.fromisoformat('2026-11-09T09:20:40+05:30')
        save_snapshot(data)
        reference = get_920_baseline(day)
        self.assertEqual(reference['day'], '2026-11-09')
        self.assertEqual(reference['snapshot']['id'], first_id)
        self.assertEqual(len(reference['snapshot']['rows']), 3)
        self.assertIsNone(get_920_baseline(date(2026, 11, 10))['snapshot'])

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

    def test_api_reads_snapshot_and_accepts_refresh_whenever_collector_is_online(self):
        import json
        import threading
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError
        from http.server import ThreadingHTTPServer
        from services.api import Handler
        from services.store import save_snapshot, state_update
        save_snapshot(self.snapshot())
        state_update(collector_available=False, loading=False, collection_status='Collector stopped')
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
            state_update(collector_available=True, loading=False, collection_status='Continuous collection')
            with urlopen(Request(url+'/api/refresh', method='POST')) as response:
                self.assertEqual(response.status, 202)
            self.assertGreater(self.admin.execute('SELECT count(*) FROM refresh_requests WHERE handled_at IS NULL').fetchone()[0], 0)
            with urlopen(url+'/api/snapshots/'+state['snapshot']['id']+'/raw') as response:
                self.assertIn('records', json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
