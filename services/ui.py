"""UI web server and same-origin API proxy; no database or market-data access."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def proxy(self):
        origin = self.headers.get('Origin')
        if self.command == 'POST' and origin and origin != 'http://'+self.headers.get('Host', ''):
            return self.reply(403, b'{"error":"Origin not allowed"}')
        headers = {'Origin': origin} if origin else {}
        request = Request(self.server.api_url+self.path, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=10) as response:
                self.reply(response.status, response.read(), response.headers.get('Content-Type', 'application/json'))
        except HTTPError as exc:
            self.reply(exc.code, exc.read())
        except (URLError, TimeoutError):
            self.reply(503, b'{"error":"API service unavailable"}')

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/':
            self.reply(200, (ROOT/'ui'/'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif route.startswith('/api/') or route == '/health':
            self.proxy()
        else:
            self.reply(404, b'{"error":"Not found"}')

    def do_POST(self):
        if self.path == '/api/refresh':
            self.proxy()
        else:
            self.reply(404, b'{"error":"Not found"}')

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--api-url', default=os.getenv('API_URL', 'http://127.0.0.1:8000'))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.api_url = args.api_url.rstrip('/')
    print(f'NIFTY UI: http://localhost:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
