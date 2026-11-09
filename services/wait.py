"""Bounded database/schema readiness for Kubernetes Jobs and init containers."""
import argparse
import os
import time
import psycopg
from . import config


def wait_for(url_env=None, token=None, timeout=600):
    deadline = time.monotonic()+timeout
    while True:
        try:
            url = os.environ.get(url_env, '') if url_env else ''
            with psycopg.connect(url, connect_timeout=3) as conn:
                if token:
                    if not conn.execute('SELECT 1 FROM app_schema_releases WHERE token=%s', (token,)).fetchone():
                        raise RuntimeError('Waiting for this release migration')
                else:
                    conn.execute('SELECT 1')
            return
        except (psycopg.Error, RuntimeError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Database/schema readiness timed out; check migration Job and database status') from None
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url-env')
    parser.add_argument('--token', default=os.getenv('MIGRATION_TOKEN'))
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    wait_for(args.url_env, args.token, args.timeout)
    print('Database/schema ready.', flush=True)


if __name__ == '__main__':
    main()
