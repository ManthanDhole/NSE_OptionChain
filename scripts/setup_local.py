"""Create an isolated native PostgreSQL cluster, credentials and application schema."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-only', action='store_true', help='Create credentials for Docker Compose without starting native PostgreSQL')
    args = parser.parse_args()
    os.chdir(ROOT)
    env_path = ROOT/'.env'
    if not env_path.exists():
        admin, collector, api = (secrets.token_hex(24) for _ in range(3))
        values = {'POSTGRES_PASSWORD': admin, 'COLLECTOR_PASSWORD': collector, 'API_PASSWORD': api,
                  'ADMIN_DATABASE_URL': f'postgresql://nifty_admin:{admin}@127.0.0.1:55432/nifty',
                  'DATABASE_URL': f'postgresql://nifty_collector:{collector}@127.0.0.1:55432/nifty',
                  'API_DATABASE_URL': f'postgresql://nifty_api:{api}@127.0.0.1:55432/nifty',
                  'REFRESH_SECONDS': '5', 'API_URL': 'http://127.0.0.1:8000'}
        descriptor = os.open(env_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(''.join(f'{key}={value}\n' for key, value in values.items()))
        print('Generated private credentials in .env (excluded from Git).')
    if args.env_only:
        return
    from dotenv import dotenv_values
    config = dotenv_values(env_path)
    for command in ('initdb', 'pg_ctl'):
        if not shutil.which(command):
            raise RuntimeError(f'{command} is missing. Install PostgreSQL or use Docker Compose.')
    folder = ROOT/'.local'
    folder.mkdir(exist_ok=True)
    data = folder/'pgdata'
    if not (data/'PG_VERSION').exists():
        password_file = folder/'init-password'
        password_file.write_text(config['POSTGRES_PASSWORD'])
        password_file.chmod(0o600)
        try:
            subprocess.run(['initdb', '-D', str(data), '-U', 'nifty_admin', '--pwfile='+str(password_file), '--auth-local=scram-sha-256', '--auth-host=scram-sha-256', '-E', 'UTF8', '--locale=C'], check=True)
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run(['pg_ctl', '-D', str(data), 'status'], capture_output=True)
    if status.returncode:
        subprocess.run(['pg_ctl', '-D', str(data), '-l', str(folder/'postgres.log'), '-o', '-h 127.0.0.1 -p 55432', 'start'], check=True)
    import psycopg
    from psycopg import sql
    with psycopg.connect(config['ADMIN_DATABASE_URL'], dbname='postgres', autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='nifty'").fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier('nifty')))
    subprocess.run([sys.executable, '-m', 'services.migrate'], check=True)
    print('Local PostgreSQL ready on 127.0.0.1:55432; database: nifty.')


if __name__ == '__main__':
    main()
