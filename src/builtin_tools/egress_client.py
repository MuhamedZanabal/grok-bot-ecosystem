"""Worker-side client for the host egress proxy (see tools/egress.py). Stdlib only.
This file runs INSIDE the sandbox; it has no other route to the network."""

import socket

SOCKET_PATH = "/run/egress/sock"


class EgressDenied(OSError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def open_connection(host: str, port: int, timeout: float = 10.0) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
    except OSError:
        s.close()
        raise EgressDenied("no_egress_for_capability") from None
    s.sendall(f"CONNECT {host}:{port}\n".encode("ascii"))
    reply = b""
    while not reply.endswith(b"\n") and len(reply) < 128:  # byte-wise: never eat tunnel data
        c = s.recv(1)
        if not c:
            break
        reply += c
    if reply == b"OK\n":
        return s
    s.close()
    text = reply.decode("ascii", "replace").strip()
    raise EgressDenied(text[len("DENIED "):] if text.startswith("DENIED ") else "proxy_protocol_error")
