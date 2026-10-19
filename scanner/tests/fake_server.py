"""A stand-in album server: records requests and answers from a script."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    """``answers`` is a list of (method, path prefix, status, body) consumed in order when they match;
    anything else is answered 200 with ``{}``, or 201 for a shot upload."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict, bytes]] = []
        self.answers: list[tuple[str, str, int, bytes]] = []
        self.json: dict[str, object] = {}  # GET path -> body
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.requests.append((self.command, self.path, self.headers, body))
                status, out = 200, b"{}"
                for i, (m, prefix, st, b) in enumerate(fake.answers):
                    if m == self.command and self.path.startswith(prefix):
                        status, out = st, b
                        del fake.answers[i]
                        break
                else:
                    if self.command == "GET" and self.path in fake.json:
                        out = json.dumps(fake.json[self.path]).encode()
                    elif self.command == "PUT" and "/shots/" in self.path:
                        status = 201
                    elif self.command == "DELETE":
                        status, out = 204, b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(out)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_PUT = do_DELETE = do_POST = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def answer(self, method: str, prefix: str, status: int, body: dict | bytes | None = None) -> None:
        b = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
        self.answers.append((method, prefix, status, b))

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
