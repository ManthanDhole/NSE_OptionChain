"""Database-backed HTTP API. No provider imports or collection threads."""
import argparse
import csv
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import logging
import os
from urllib.parse import urlsplit, parse_qs
from uuid import UUID
from .config import INTERVAL
from .store import connect, get_snapshot, runtime_state


def encode(value):
    return json.dumps(value, default=str).encode()


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, data, content_type='application/json'):
        body = data if isinstance(data, bytes) else encode(data)
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            route = urlsplit(self.path)
            params = parse_qs(route.query)
            if route.path == '/health':
                with connect(True) as conn:
                    conn.execute('SELECT 1')
                return self.reply(200, {'status': 'ok'})
            if route.path == '/api/state':
                snapshot = get_snapshot()
                state = runtime_state()
                return self.reply(200, {**state, 'snapshot': snapshot, 'cached': False,
                                       'last_success': snapshot['collected_at'] if snapshot else None,
                                       'warnings': snapshot['warnings'] if snapshot else [],
                                       'refresh_seconds': INTERVAL})
            if route.path == '/api/snapshots':
                limit = max(1, min(500, int(params.get('limit', ['100'])[0])))
                clauses, args = [], []
                if 'date' in params:
                    day = date.fromisoformat(params['date'][0])
                    clauses.append("(collected_at AT TIME ZONE 'Asia/Kolkata')::date=%s")
                    args.append(day)
                if 'before' in params:
                    from datetime import datetime
                    clauses.append('collected_at<%s')
                    args.append(datetime.fromisoformat(params['before'][0]))
                query = 'SELECT id,collected_at,expiry,spot,index_rsi,market_timestamp FROM snapshots'
                if clauses:
                    query += ' WHERE '+' AND '.join(clauses)
                args.append(limit)
                with connect(True) as conn:
                    rows = conn.execute(query+' ORDER BY collected_at DESC,id DESC LIMIT %s', args).fetchall()
                return self.reply(200, {'snapshots': rows})
            if route.path.startswith('/api/snapshots/'):
                parts = route.path.strip('/').split('/')
                snapshot_id = UUID(parts[2])
                if len(parts) == 4 and parts[3] == 'raw':
                    with connect(True) as conn:
                        row = conn.execute('SELECT raw_payload FROM snapshots WHERE id=%s', (snapshot_id,)).fetchone()
                    return self.reply(200, row['raw_payload']) if row else self.reply(404, {'error': 'Snapshot not found'})
                snapshot = get_snapshot(snapshot_id, full=True)
                return self.reply(200, snapshot) if snapshot else self.reply(404, {'error': 'Snapshot not found'})
            if route.path == '/api/download':
                snapshot = get_snapshot(UUID(params['id'][0]) if 'id' in params else None, full=True)
                if not snapshot:
                    return self.reply(404, {'error': 'No stored snapshot'})
                rows = snapshot['rows']
                fields = list(dict.fromkeys(key for row in rows for key in row))
                buffer = StringIO()
                writer = csv.DictWriter(buffer, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                return self.reply(200, buffer.getvalue().encode(), 'text/csv; charset=utf-8')
            self.reply(404, {'error': 'Not found'})
        except (ValueError, IndexError) as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            logging.exception('API request failed')
            self.reply(503, {'error': 'Database unavailable. The UI will retry.'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed = os.getenv('UI_ORIGIN', 'http://localhost:8765')
        if origin and origin not in (allowed, 'http://127.0.0.1:8765'):
            return self.reply(403, {'error': 'Origin not allowed'})
        if self.path != '/api/refresh':
            return self.reply(404, {'error': 'Not found'})
        try:
            state = runtime_state()
            if not state.get('market_open'):
                return self.reply(409, {'error': state.get('schedule_reason', 'Collection is not available')})
            if not state.get('loading'):
                with connect(True) as conn:
                    conn.execute('INSERT INTO refresh_requests DEFAULT VALUES')
            self.reply(202, {'accepted': not state.get('loading', False)})
        except Exception:
            logging.exception('Refresh request failed')
            self.reply(503, {'error': 'Database unavailable'})

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'PostgreSQL API: http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
