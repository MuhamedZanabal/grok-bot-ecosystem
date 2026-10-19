"""Local fixture HTTP servers for egress/browser tests (no live external sites)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = (b"<html><head><title>Fixture Page</title><style>.x{}</style></head><body>"
        b"<h1>Hello</h1><p>Visible <b>text</b> &amp; more.</p><script>steal()</script>"
        b"<a href='/next'>next</a> <a href='http://other.invalid/z'>ext</a></body></html>")


class Fixture:
    """An HTTP server on 127.0.0.1:<ephemeral> that records every request it receives."""

    def __init__(self, routes=None) -> None:
        self.hits: list[str] = []
        self.routes = routes or {}
        fx = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                fx.hits.append(self.path)
                route = fx.routes.get(self.path)
                if route is not None:
                    return route(self)
                if self.path.startswith("/page") or self.path == "/next":
                    return self._send(200, PAGE)
                self._send(404, b"nope")

            def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.entry = f"127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def redirect(location: str):
    def route(h):
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return route


def big(n: int):
    def route(h):
        body = b"<html><body>" + b"A" * n + b"</body></html>"
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        try:
            h.wfile.write(body)
        except OSError:
            pass
    return route
