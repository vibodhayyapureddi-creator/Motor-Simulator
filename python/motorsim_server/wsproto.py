"""Minimal server-side WebSocket (RFC 6455) implementation, stdlib only.

The interactive app deliberately avoids third-party web dependencies, so
instead of FastAPI/websockets this module implements the small slice of
RFC 6455 the app needs:

- the HTTP Upgrade handshake (server side),
- reading masked client frames (text, binary, ping, pong, close),
  including fragmented messages,
- writing unmasked server frames.

It is not a general-purpose WebSocket library: no extensions
(permessage-deflate etc.), no client-side handshake. Payloads in this app
are small JSON messages, so simplicity beats completeness.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Optional

# Fixed GUID from RFC 6455 section 1.3, used to compute Sec-WebSocket-Accept.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_CONTROL_OPS = (OP_CLOSE, OP_PING, OP_PONG)


class WebSocketError(Exception):
    """Protocol violation or unexpected socket closure."""


def make_accept_key(client_key: str) -> str:
    """Compute the Sec-WebSocket-Accept value for a client's key."""
    digest = hashlib.sha1((client_key.strip() + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(client_key: str) -> bytes:
    """The complete 101 Switching Protocols response for a client key."""
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {make_accept_key(client_key)}\r\n"
        "\r\n"
    ).encode("ascii")


def encode_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """Encode a single unmasked (server -> client) frame."""
    header = bytearray()
    header.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < (1 << 16):
        header.append(126)
        header += struct.pack("!H", n)
    else:
        header.append(127)
        header += struct.pack("!Q", n)
    return bytes(header) + payload


@dataclass
class Frame:
    fin: bool
    opcode: int
    payload: bytes


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes or raise WebSocketError on EOF."""
    chunks = bytearray()
    while len(chunks) < n:
        part = sock.recv(n - len(chunks))
        if not part:
            raise WebSocketError("socket closed mid-frame")
        chunks += part
    return bytes(chunks)


def read_frame(sock: socket.socket, max_payload: int = 1 << 20) -> Frame:
    """Read one frame from a client. Client frames must be masked (RFC 6455)."""
    b1, b2 = _recv_exact(sock, 2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _recv_exact(sock, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _recv_exact(sock, 8))
    if length > max_payload:
        raise WebSocketError(f"frame payload {length} exceeds limit {max_payload}")
    if not masked:
        # Clients MUST mask; treat unmasked as a protocol error.
        raise WebSocketError("client frame not masked")
    mask = _recv_exact(sock, 4)
    payload = bytearray(_recv_exact(sock, length))
    for i in range(length):
        payload[i] ^= mask[i & 3]
    return Frame(fin=fin, opcode=opcode, payload=bytes(payload))


class WebSocketConnection:
    """A server-side connection over an already-upgraded socket.

    Reading happens on whatever thread calls recv_message(); sends are
    serialized with a lock so the simulation broadcaster and the reader's
    control-frame replies (pong/close) can share the socket safely.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._send_lock = threading.Lock()
        self.open = True

    def send_text(self, text: str) -> None:
        self._send(OP_TEXT, text.encode("utf-8"))

    def send_ping(self, payload: bytes = b"") -> None:
        self._send(OP_PING, payload)

    def _send(self, opcode: int, payload: bytes) -> None:
        if not self.open:
            raise WebSocketError("connection closed")
        data = encode_frame(opcode, payload)
        with self._send_lock:
            self._sock.sendall(data)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if not self.open:
            return
        try:
            payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
            self._send(OP_CLOSE, payload)
        except OSError:
            pass
        except WebSocketError:
            pass
        finally:
            self.open = False
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()

    def recv_message(self) -> Optional[str]:
        """Block until a complete text message arrives.

        Returns None when the client closes the connection. Ping frames are
        answered automatically; pong and binary frames are ignored.
        """
        message = bytearray()
        while True:
            try:
                frame = read_frame(self._sock)
            except (OSError, WebSocketError):
                self.open = False
                return None

            if frame.opcode in _CONTROL_OPS:
                if frame.opcode == OP_CLOSE:
                    self.close()
                    return None
                if frame.opcode == OP_PING:
                    try:
                        self._send(OP_PONG, frame.payload)
                    except (OSError, WebSocketError):
                        self.open = False
                        return None
                continue  # pong: ignore

            if frame.opcode in (OP_TEXT, OP_BINARY):
                message = bytearray(frame.payload)
            elif frame.opcode == OP_CONT:
                message += frame.payload
            else:
                raise WebSocketError(f"unsupported opcode {frame.opcode}")

            if frame.fin:
                return message.decode("utf-8", errors="replace")
