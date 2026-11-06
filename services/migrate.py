"""Initialize schema and narrowly scoped service roles using the admin URL."""
import os
import psycopg
from psycopg import sql
from .config import ROOT


def main():
    url = os.getenv('ADMIN_DATABASE_URL')
    if not url:
        raise RuntimeError('ADMIN_DATABASE_URL is required for migrations')
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute((ROOT/'database'/'schema.sql').read_text())
        for role, key in (('nifty_collector', 'COLLECTOR_PASSWORD'), ('nifty_api', 'API_PASSWORD')):
            password = os.environ[key]
            exists = conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            action = 'ALTER ROLE' if exists else 'CREATE ROLE'
            conn.execute(sql.SQL(action+' {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
            conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(conn.info.dbname), sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        conn.execute('GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO nifty_collector')
        conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO nifty_collector')
        conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO nifty_api')
        conn.execute('GRANT INSERT ON refresh_requests TO nifty_api')
        conn.execute('GRANT USAGE ON SEQUENCE refresh_requests_id_seq TO nifty_api')
    print('PostgreSQL schema and service roles ready.')


if __name__ == '__main__':
    main()
