"""Host-side, deny-by-default egress proxy for sandboxed tool workers.

Workers keep `--unshare-net`, so they have no route anywhere. A worker whose WorkerSpec
lists `egress` entries gets ONE Unix socket bound read-only at /run/egress/sock. It is served
by an EgressProxy running in the host (control-plane) process for the duration of that call.

Protocol (one line, then raw bytes): the worker sends `CONNECT <host>:<port>\\n`. The
proxy answers `OK\\n` and splices bytes both ways, or answers `DENIED <reason>\\n` and closes.

Checks, in order:
  1. strict grammar (lowercase DNS name or IPv4 literal, port 1-65535 with no leading zeros,
     no CR, no extra tokens, <= 300 bytes);
  2. per-call connection budget;
  3. exact match against the capability's allowlist. This happens BEFORE any DNS resolution,
     so a non-allowlisted name is never resolved or contacted (no DNS exfiltration);
  4. resolve on the host; a *name* entry must resolve to a globally routable address
     (rebinding guard). An IPv4 literal entry is an explicit operator choice and is used as is;
  5. a per-call byte budget across all connections.
Every attempt is audited (`egress_allowed`, `egress_denied`, `egress_failed`), and so is
every close (`egress_closed`, with byte counts).
"""

import hashlib
import ipaddress
import os
import re
import select
import socket
import threading
import time

SOCKET_DIR_IN_SANDBOX = "/run/egress"
SOCKET_NAME = "sock"
_HOST = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
_PORT = r"[1-9][0-9]{0,4}"
ENTRY = re.compile(rf"({_HOST}):({_PORT})")
LINE = re.compile(rf"CONNECT ({_HOST}):({_PORT})")
MAX_LINE = 300


def valid_entry(entry: str) -> bool:
    m = ENTRY.fullmatch(entry) if isinstance(entry, str) else None
    return bool(m) and len(m.group(1)) <= 253 and int(m.group(2)) <= 65535


def _is_ipv4_literal(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


def _resolve(host: str, port: int) -> str:
    """Host-side resolution (patched in tests to count calls)."""
    return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


class EgressProxy:
    def __init__(self, allow: tuple[str, ...], audit, capability: str, *, max_connections: int = 16,
                 max_bytes: int = 8 * 1024 * 1024, connect_timeout: float = 5.0, idle_timeout: float = 15.0) -> None:
        if audit is None:
            raise ValueError("egress_requires_audit")
        for e in allow:
            if not valid_entry(e):
                raise ValueError(f"bad egress entry {e!r}")
        self._allow = frozenset(allow)
        self._audit, self._cap = audit, capability
        self._max_conn, self._max_bytes = max_connections, max_bytes
        self._connect_timeout, self._idle = connect_timeout, idle_timeout
        self._lock = threading.Lock()
        self._conns = 0
        self._bytes = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._listener: socket.socket | None = None
        self._wake_r, self._wake_w = os.pipe()  # stop() wakes every select() at once
        self.attempts = 0

    # -- lifecycle -----------------------------------------------------------------------
    def start(self, directory: str) -> str:
        path = os.path.join(directory, SOCKET_NAME)
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(path)
        self._listener.listen(16)
        t = threading.Thread(target=self._accept_loop, name="egress-accept", daemon=True)
        t.start()
        self._threads.append(t)
        return path

    def stop(self) -> None:
        self._stop.set()
        os.write(self._wake_w, b"x")
        for t in list(self._threads):
            t.join(timeout=2)
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        os.close(self._wake_r)
        os.close(self._wake_w)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            r, _, _ = select.select([self._listener, self._wake_r], [], [])
            if self._wake_r in r:
                return
            try:
                conn, _ = self._listener.accept()
            except OSError:
                continue
            t = threading.Thread(target=self._handle, args=(conn,), name="egress-conn", daemon=True)
            self._threads.append(t)
            t.start()

    # -- per connection ------------------------------------------------------------------
    def _event(self, event: str, **fields) -> None:
        self._audit.append(event, capability=self._cap, **fields)

    def _deny(self, conn: socket.socket, reason: str, **fields) -> None:
        self._event("egress_denied", reason=reason, **fields)
        try:
            conn.sendall(f"DENIED {reason}\n".encode())
        except OSError:
            pass
        conn.close()

    def _read_line(self, conn: socket.socket) -> tuple[bytes | None, bytes]:
        conn.settimeout(self._connect_timeout)
        buf = b""
        try:
            while b"\n" not in buf and len(buf) <= MAX_LINE:
                chunk = conn.recv(MAX_LINE + 1 - len(buf))
                if not chunk:
                    return None, b""
                buf += chunk
        except OSError:
            return None, b""
        if b"\n" not in buf:
            return None, buf
        line, rest = buf.split(b"\n", 1)
        return line, rest

    def _handle(self, conn: socket.socket) -> None:
        with self._lock:
            self.attempts += 1
        line, rest = self._read_line(conn)
        m = None
        try:
            m = LINE.fullmatch(line.decode("ascii")) if line is not None else None
        except UnicodeDecodeError:
            m = None
        if m is None or len(m.group(1)) > 253 or int(m.group(2)) > 65535:
            raw = line if line is not None else rest
            return self._deny(conn, "malformed", line_sha256=hashlib.sha256(raw).hexdigest()[:16], line_len=len(raw))
        host, port_s = m.group(1), m.group(2)
        port = int(port_s)
        target = f"{host}:{port_s}"
        with self._lock:
            self._conns += 1
            over = self._conns > self._max_conn
        if over:
            return self._deny(conn, "connection_budget", host=host, port=port)
        if target not in self._allow:
            return self._deny(conn, "not_allowlisted", host=host, port=port)
        literal = _is_ipv4_literal(host)
        try:
            ip = host if literal else _resolve(host, port)
        except OSError:
            return self._deny(conn, "resolve_failed", host=host, port=port)
        if not literal and not ipaddress.ip_address(ip).is_global:
            return self._deny(conn, "resolved_non_global", host=host, port=port, ip=ip)
        try:
            upstream = socket.create_connection((ip, port), timeout=self._connect_timeout)
        except OSError as exc:
            self._event("egress_failed", host=host, port=port, ip=ip, error=type(exc).__name__)
            try:
                conn.sendall(b"DENIED connect_failed\n")
            except OSError:
                pass
            conn.close()
            return
        self._event("egress_allowed", host=host, port=port, ip=ip)
        try:
            conn.sendall(b"OK\n")
            if rest:
                upstream.sendall(rest)
            out_b, in_b, exhausted = self._splice(conn, upstream, len(rest))
        finally:
            upstream.close()
            conn.close()
        self._event("egress_closed", host=host, port=port, bytes_out=out_b, bytes_in=in_b,
                    budget_exhausted=exhausted)

    def _charge(self, n: int) -> bool:
        with self._lock:
            self._bytes += n
            return self._bytes > self._max_bytes

    def _splice(self, a: socket.socket, b: socket.socket, already_out: int) -> tuple[int, int, bool]:
        out_b, in_b = already_out, 0
        if self._charge(already_out):
            return out_b, in_b, True
        a.settimeout(None)
        b.settimeout(None)
        peers = {a: b, b: a}
        last = time.monotonic()
        while not self._stop.is_set() and time.monotonic() - last < self._idle:
            r, _, _ = select.select([a, b, self._wake_r], [], [], 0.5)
            if self._wake_r in r:
                break
            for s in r:
                last = time.monotonic()
                try:
                    data = s.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    return out_b, in_b, False
                if self._charge(len(data)):
                    return out_b, in_b, True
                if s is a:
                    out_b += len(data)
                else:
                    in_b += len(data)
                try:
                    peers[s].sendall(data)
                except OSError:
                    return out_b, in_b, False
        return out_b, in_b, False
