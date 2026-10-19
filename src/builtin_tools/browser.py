"""cap.browser.fetch: a minimal, sandboxed browser tier (stdlib only; runs INSIDE the sandbox).

GET a URL through the host egress proxy and return the page as data: status, final URL,
title, visible text and links. Redirects are followed here, hop by hop, so every hop is a
separate CONNECT that the host proxy checks and audits. A hop to a non-allowlisted host or
port is DENIED there and reported as `blocked`. No cookies, no credentials, no JavaScript.
Page content is untrusted data (prompt-injection vector) and is returned as such.
"""

import http.client
from html.parser import HTMLParser
import re
import ssl
from urllib.parse import urljoin, urlsplit

from egress_client import EgressDenied, open_connection

MAX_URL = 2048
REDIRECTS = {301, 302, 303, 307, 308}
_CHARSET = re.compile(r"charset=([A-Za-z0-9_-]{1,40})")


class _Extract(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self, base: str, max_links: int, max_text: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base, self.max_links, self.max_text = base, max_links, max_text
        self.title, self.text, self.links = "", [], []
        self._skip = 0
        self._in_title = False
        self._len = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and len(self.links) < self.max_links:
            href = dict(attrs).get("href")
            if href:
                self.links.append(urljoin(self.base, href)[:MAX_URL])

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title = (self.title + data).strip()[:300]
            return
        data = " ".join(data.split())
        if data and self._len < self.max_text:
            self.text.append(data[: self.max_text - self._len])
            self._len += len(data)


def _target(url: str):
    if not isinstance(url, str) or len(url) > MAX_URL:
        raise ValueError("bad_url")
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("unsupported_scheme")
    if u.username is not None or u.password is not None or not u.hostname:
        raise ValueError("bad_url")
    port = u.port or (443 if u.scheme == "https" else 80)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return u.scheme, u.hostname.lower(), port, path


SYSTEM_CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")


def tls_context() -> ssl.SSLContext:
    """Verifying context. Interpreters with a bundled OpenSSL may not look in /etc/ssl by
    default, so fall back to the system bundle (mounted read-only into egress sandboxes)."""
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca"):
        for path in SYSTEM_CA_BUNDLES:
            try:
                ctx.load_verify_locations(cafile=path)
                break
            except OSError:
                continue
    return ctx


def tls_roots(args):
    """Diagnostic tool function: how many CA roots the worker's TLS context trusts."""
    return {"x509_ca": tls_context().cert_store_stats().get("x509_ca", 0)}


def _get(scheme, host, port, path, max_bytes, timeout):
    sock = open_connection(host, port, timeout)
    if scheme == "https":
        sock = tls_context().wrap_socket(sock, server_hostname=host)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock
    try:
        conn.request("GET", path, headers={"User-Agent": "grok-bot-fetch/0.1", "Accept": "text/html,*/*;q=0.5",
                                           "Connection": "close"})
        resp = conn.getresponse()
        body = resp.read(max_bytes + 1)
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


def fetch(args):
    max_bytes = int(args.get("max_bytes", 512 * 1024))
    max_redirects = int(args.get("max_redirects", 5))
    timeout = float(args.get("timeout_s", 10))
    url = args["url"]
    chain = []
    for _ in range(max_redirects + 1):
        try:
            scheme, host, port, path = _target(url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "url": url[:MAX_URL], "chain": chain}
        chain.append(url[:MAX_URL])
        try:
            status, headers, body = _get(scheme, host, port, path, max_bytes, timeout)
        except EgressDenied as exc:
            return {"ok": False, "error": "blocked", "blocked": {"host": host, "port": port, "reason": exc.reason},
                    "chain": chain}
        except ssl.SSLCertVerificationError:
            return {"ok": False, "error": "tls_verify_failed", "chain": chain}
        except (OSError, http.client.HTTPException) as exc:
            return {"ok": False, "error": f"fetch_failed:{type(exc).__name__}", "chain": chain}
        if status in REDIRECTS and headers.get("location"):
            url = urljoin(url, headers["location"])
            continue
        truncated = len(body) > max_bytes
        body = body[:max_bytes]
        m = _CHARSET.search(headers.get("content-type", ""))
        try:
            html = body.decode(m.group(1) if m else "utf-8", "replace")
        except LookupError:
            html = body.decode("utf-8", "replace")
        ex = _Extract(url, int(args.get("max_links", 100)), int(args.get("max_text", 20000)))
        ex.feed(html)
        ex.close()
        return {"ok": True, "status": status, "url": url, "chain": chain, "title": ex.title,
                "text": " ".join(ex.text), "links": ex.links, "bytes": len(body), "truncated": truncated,
                "content_type": headers.get("content-type", "")[:100]}
    return {"ok": False, "error": "too_many_redirects", "chain": chain}
