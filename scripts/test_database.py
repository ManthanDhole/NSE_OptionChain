"""Run integration tests in a disposable database, never the live market database."""
import os
from pathlib import Path
import subprocess
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.config import ROOT
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main():
    os.chdir(ROOT)
    name = 'nifty_test_'+uuid.uuid4().hex[:12]
    admin_url = os.environ['ADMIN_DATABASE_URL']
    env = dict(os.environ, RUN_DATABASE_TESTS='1', PYTHONDONTWRITEBYTECODE='1')
    for key in ('ADMIN_DATABASE_URL', 'DATABASE_URL', 'API_DATABASE_URL'):
        env[key] = make_conninfo(os.environ[key], dbname=name)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            subprocess.run([sys.executable, '-m', 'services.migrate'], env=env, check=True)
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=env)
        finally:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
