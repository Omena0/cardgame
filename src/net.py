from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from dataclasses import dataclass

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_http_header(sock: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _parse_headers(raw: bytes) -> dict[str, str]:
    text = raw.decode("latin1", errors="ignore")
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _frame_text(payload: str, masked: bool) -> bytes:
    data = payload.encode("utf-8")
    first_byte = 0x80 | 0x1
    length = len(data)
    mask_bit = 0x80 if masked else 0
    frame = bytearray([first_byte])
    if length < 126:
        frame.append(mask_bit | length)
    elif length < (1 << 16):
        frame.append(mask_bit | 126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(mask_bit | 127)
        frame.extend(struct.pack("!Q", length))

    if masked:
        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(data))
    else:
        frame.extend(data)
    return bytes(frame)


@dataclass
class WebSocketConnection:
    sock: socket.socket
    masked: bool = False

    def __post_init__(self) -> None:
        self.sock.setblocking(True)
        self._send_lock = threading.Lock()
        self._recv_buffer = bytearray()
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, payload: str) -> None:
        if self._closed:
            return
        with self._send_lock:
            self.sock.sendall(_frame_text(payload, self.masked))

    def _read_exact(self, size: int) -> bytes:
        while len(self._recv_buffer) < size:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._recv_buffer.extend(chunk)
        result = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return result

    def recv_text(self) -> str | None:
        first_two = self._read_exact(2)
        first_byte, second_byte = first_two[0], first_two[1]
        opcode = first_byte & 0x0F
        masked = bool(second_byte & 0x80)
        length = second_byte & 0x7F

        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]

        mask = self._read_exact(4) if masked else b""
        payload = bytearray(self._read_exact(length))
        if masked:
            for index in range(length):
                payload[index] ^= mask[index % 4]

        if opcode == 0x8:
            self.close()
            return None
        if opcode == 0x9:
            self._send_control(0xA, bytes(payload))
            return self.recv_text()
        if opcode != 0x1:
            return self.recv_text()

        return payload.decode("utf-8", errors="replace")

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        if self._closed:
            return
        first_byte = 0x80 | opcode
        length = len(payload)
        frame = bytearray([first_byte])
        if length < 126:
            frame.append(length)
        elif length < (1 << 16):
            frame.append(126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", length))
        frame.extend(payload)
        with self._send_lock:
            self.sock.sendall(bytes(frame))


def accept_websocket(client_socket: socket.socket) -> WebSocketConnection:
    request = _read_http_header(client_socket)
    headers = _parse_headers(request)
    key = headers.get("sec-websocket-key")
    if not key:
        raise ConnectionError("missing websocket key")

    accept_value = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_value}\r\n"
        "\r\n"
    )
    client_socket.sendall(response.encode("ascii"))
    return WebSocketConnection(client_socket, masked=False)


def connect_websocket(host: str, port: int, path: str = "/") -> WebSocketConnection:
    sock = socket.create_connection((host, port))
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = _read_http_header(sock)
    if b"101" not in response.split(b"\r\n", 1)[0]:
        raise ConnectionError("websocket handshake failed")
    return WebSocketConnection(sock, masked=True)

