import os
import unittest
from unittest.mock import patch, MagicMock
from services.config import database_url
from services.wait import wait_for


class KubernetesConfigurationTests(unittest.TestCase):
    def test_libpq_environment_without_url(self):
        with patch.dict(os.environ, {'PGHOST': 'postgres', 'PGPASSWORD': 'special:@/?password'}, clear=True):
            self.assertEqual(database_url(), '')
            self.assertEqual(database_url(reader=True), '')

    def test_explicit_url_takes_precedence(self):
        with patch.dict(os.environ, {'PGHOST': 'postgres', 'DATABASE_URL': 'postgresql://explicit'}, clear=True):
            self.assertEqual(database_url(), 'postgresql://explicit')

    def test_wait_requires_exact_release_migration(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.side_effect = [None, (1,)]
        with patch('services.wait.psycopg.connect', return_value=connection), patch('services.wait.time.sleep') as sleep:
            wait_for(token='nifty-r2', timeout=5)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(connection.execute.call_args.args[1], ('nifty-r2',))

    def test_wait_fails_with_bounded_error(self):
        import psycopg
        with patch('services.wait.psycopg.connect', side_effect=psycopg.OperationalError('hidden detail')), patch('services.wait.time.monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(RuntimeError, 'readiness timed out'):
                wait_for(timeout=0)
