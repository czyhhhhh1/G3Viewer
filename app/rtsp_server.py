"""Minimal low-latency RTSP/RTP H.264 server for Mission Planner.

Mission Planner's HUD can consume an RTSP H.264 stream through GStreamer:
    rtspsrc location=rtsp://127.0.0.1:8554/fpv_stream ...
This server takes the already-received Annex B H.264 access units, packetizes
them as RTP/H.264 (single NAL + FU-A), and serves them over RTSP without
re-encoding or an extra process.

Only UDP RTP transport is implemented; it is the default for GStreamer's
rtspsrc and VLC. The stream is local, so packet loss is negligible.
"""
from __future__ import annotations

import base64
import queue
import random
import socket
import struct
import threading
import time
from collections import deque
from typing import Callable

RTSP_PORT = 8554
RTSP_PATH = "/fpv_stream"
MAX_RTP_PAYLOAD = 1200


def split_annexb(data: bytes) -> list[bytes]:
    """Split an Annex B byte stream into NAL units (without start codes).

    Uses bytes.find (C-level) rather than a Python byte loop: this runs on every
    high-bitrate frame, so a per-byte Python scan would steal GIL time from the
    decoder.
    """
    starts: list[tuple[int, int]] = []
    pos = 0
    while True:
        found = data.find(b"\x00\x00\x01", pos)
        if found < 0:
            break
        if found > 0 and data[found - 1] == 0:
            starts.append((found - 1, 4))
        else:
            starts.append((found, 3))
        pos = found + 3
    nals = []
    for idx, (start, sc) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(data)
        nal = data[start + sc : end].rstrip(b"\x00")
        if nal:
            nals.append(nal)
    return nals


def _fu_a(nal: bytes, ssrc: int, seq_start: int, ts: int) -> list[bytes]:
    """Fragment one NAL into RTP FU-A packets. Returns full RTP packets."""
    header = nal[0]
    nal_type = header & 0x1F
    fu_indicator = (header & 0xE0) | 28
    payload = nal[1:]
    chunks = [payload[i : i + MAX_RTP_PAYLOAD] for i in range(0, len(payload), MAX_RTP_PAYLOAD)]
    packets = []
    seq = seq_start
    for idx, chunk in enumerate(chunks):
        start = idx == 0
        end = idx == len(chunks) - 1
        fu_header = (0x80 if start else 0) | (0x40 if end else 0) | nal_type
        body = bytes([fu_indicator, fu_header]) + chunk
        packets.append(_rtp_packet(ssrc, seq, ts, body, marker=False))
        seq = (seq + 1) & 0xFFFF
    return packets


def _rtp_packet(ssrc: int, seq: int, ts: int, payload: bytes, marker: bool) -> bytes:
    header = struct.pack(
        "!BBHII",
        0x80,
        (0x80 if marker else 0) | 96,
        seq & 0xFFFF,
        ts & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return header + payload


class _Client:
    def __init__(self, conn: socket.socket, addr: tuple[str, int], server: "RTSPServer") -> None:
        self.conn = conn
        self.addr = addr
        self.server = server
        self.session = f"{random.randrange(1, 0x7FFFFFFF):08X}"
        self.rtp_sock: socket.socket | None = None
        self.rtcp_sock: socket.socket | None = None
        self.rtp_port = 0
        self.rtcp_port = 0
        self.playing = False
        self.closed = False
        self.ssrc = random.randrange(1, 0xFFFFFFFF)
        self.seq = random.randrange(0, 0xFFFF)
        self.timestamp = random.randrange(0, 0xFFFFFFFF)
        self.queue: deque[list[bytes]] = deque(maxlen=3)
        self.cond = threading.Condition()
        self.sender: threading.Thread | None = None
        self.dropped = 0

    def start_sender(self) -> None:
        self.sender = threading.Thread(target=self._send_loop, name="rtsp-rtp", daemon=True)
        self.sender.start()

    def enqueue(self, nals: list[bytes]) -> None:
        if not self.playing:
            return
        with self.cond:
            if len(self.queue) >= self.queue.maxlen:
                self.queue.popleft()
                self.dropped += 1
            self.queue.append(nals)
            self.cond.notify()

    def _send_loop(self) -> None:
        while not self.closed:
            with self.cond:
                while not self.queue and not self.closed:
                    self.cond.wait(0.2)
                if self.closed:
                    return
                nals = self.queue.popleft()
            if not self.rtp_sock or not self.rtp_port:
                continue
            if not any((nal[0] & 0x1F) in (1, 5) for nal in nals):
                # Never emit parameter-set-only access units; they are already
                # in the SDP sprop and make decoders log a missing picture.
                continue
            ts = self.timestamp & 0xFFFFFFFF
            self.timestamp = (self.timestamp + 3000) & 0xFFFFFFFF
            packets: list[bytes] = []
            for index, nal in enumerate(nals):
                if len(nal) <= MAX_RTP_PAYLOAD:
                    marker = index == len(nals) - 1
                    packets.append(_rtp_packet(self.ssrc, self.seq, ts, nal, marker))
                    self.seq = (self.seq + 1) & 0xFFFF
                else:
                    frags = _fu_a(nal, self.ssrc, self.seq, ts)
                    self.seq = (self.seq + len(frags)) & 0xFFFF
                    packets.extend(frags)
            # Set the marker bit on the final packet of the access unit.
            if packets:
                last = bytearray(packets[-1])
                last[1] |= 0x80
                packets[-1] = bytes(last)
            for packet in packets:
                try:
                    self.rtp_sock.sendto(packet, (self.addr[0], self.rtp_port))
                except OSError:
                    return

    def close(self) -> None:
        self.closed = True
        with self.cond:
            self.cond.notify_all()
        for sock in (self.rtp_sock, self.rtcp_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        try:
            self.conn.close()
        except OSError:
            pass


class RTSPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = RTSP_PORT, log: Callable[[str], None] | None = None) -> None:
        self.host = host
        self.port = port
        self.log = log or (lambda _m: None)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._sps: bytes | None = None
        self._pps: bytes | None = None
        self._last_idr_frame: list[bytes] | None = None
        self._params_ready = threading.Event()
        self._feed_queue: queue.Queue[bytes] = queue.Queue(maxsize=3)
        self._feed_stop = threading.Event()
        self._feed_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"rtsp://{self.host}:{self.port}{RTSP_PATH}"

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, name="rtsp-accept", daemon=True)
        self._thread.start()
        self._feed_thread = threading.Thread(target=self._feed_loop, name="rtsp-feed", daemon=True)
        self._feed_thread.start()
        self.log(f"Mission Planner RTSP: {self.url}")

    def stop(self) -> None:
        self._stop.set()
        self._feed_stop.set()
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            client.close()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=1)
        if self._feed_thread:
            self._feed_thread.join(timeout=1)

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.5)
            client = _Client(conn, (addr[0], addr[1]), self)
            with self._lock:
                self._clients.append(client)
            threading.Thread(target=self._client_loop, args=(client,), daemon=True).start()

    def push(self, frame: bytes) -> None:
        """Queue one access unit for RTSP parsing/output.

        This runs on the UDP receive thread, so it only appends to a bounded
        queue. NAL scanning and RTP packetization happen on the feed thread.
        With no RTSP client and a complete cached parameter set, skip the frame
        entirely so Mission Planner support costs nothing while unused.
        """
        with self._lock:
            has_clients = bool(self._clients)
        if not has_clients and self._sps and self._pps and self._last_idr_frame:
            return
        try:
            self._feed_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._feed_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._feed_queue.put_nowait(frame)
            except queue.Full:
                pass

    def _feed_loop(self) -> None:
        while not self._feed_stop.is_set():
            try:
                frame = self._feed_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            nals = split_annexb(frame)
            if not nals:
                continue
            for nal in nals:
                if nal[0] & 0x1F == 7:
                    self._sps = nal
                elif nal[0] & 0x1F == 8:
                    self._pps = nal
                elif nal[0] & 0x1F == 5:
                    self._last_idr_frame = nals
            if self._sps and self._pps:
                self._params_ready.set()
            # SPS/PPS-only frames are not a decodable access unit. Cache them
            # but do not send an empty picture ("missing picture" warnings).
            if not any((nal[0] & 0x1F) in (1, 5) for nal in nals):
                continue
            with self._lock:
                clients = list(self._clients)
            for client in clients:
                client.enqueue(nals)

    def _sdp(self) -> str:
        sps = self._sps or b""
        pps = self._pps or b""
        profile = sps[1:4].hex() if len(sps) >= 4 else "640034"
        sps_b64 = base64.b64encode(sps).decode()
        pps_b64 = base64.b64encode(pps).decode()
        return (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            "s=G3Viewer LiveView\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            "t=0 0\r\n"
            "m=video 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            f"a=fmtp:96 packetization-mode=1;profile-level-id={profile};"
            f"sprop-parameter-sets={sps_b64},{pps_b64}\r\n"
            "a=control:trackID=0\r\n"
        )

    @staticmethod
    def _response(cseq: str, headers: dict[str, str] | None = None, body: str = "") -> bytes:
        lines = [f"RTSP/1.0 200 OK\r\n", f"CSeq: {cseq}\r\n"]
        for key, value in (headers or {}).items():
            lines.append(f"{key}: {value}\r\n")
        if body:
            lines.append(f"Content-Length: {len(body.encode())}\r\n")
        lines.append("\r\n")
        if body:
            lines.append(body)
        return "".join(lines).encode()

    def _client_loop(self, client: _Client) -> None:
        buffer = b""
        try:
            while not self._stop.is_set() and not client.closed:
                while b"\r\n\r\n" not in buffer:
                    chunk = client.conn.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                head, buffer = buffer.split(b"\r\n\r\n", 1)
                text = head.decode("latin-1")
                lines = text.split("\r\n")
                if not lines:
                    return
                parts = lines[0].split()
                if len(parts) < 3:
                    return
                method, url, _version = parts[0], parts[1], parts[2]
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                cseq = headers.get("cseq", "0")
                if method == "OPTIONS":
                    client.conn.sendall(
                        self._response(
                            cseq,
                            {"Public": "OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN, GET_PARAMETER"},
                        )
                    )
                elif method == "DESCRIBE":
                    self._params_ready.wait(2.0)
                    sdp = self._sdp()
                    client.conn.sendall(
                        self._response(
                            cseq,
                            {
                                "Content-Type": "application/sdp",
                                "Content-Base": f"rtsp://{self.host}:{self.port}{RTSP_PATH}/",
                            },
                            sdp,
                        )
                    )
                elif method == "SETUP":
                    transport = headers.get("transport", "")
                    if "TCP" in transport.upper():
                        client.conn.sendall(self._response(cseq, {"Transport": transport}, ""))
                        continue
                    client_port = ""
                    for item in transport.split(";"):
                        if item.strip().lower().startswith("client_port="):
                            client_port = item.split("=", 1)[1]
                    try:
                        ports = [int(p) for p in client_port.split("-")[:2]]
                    except Exception:  # noqa: BLE001
                        ports = []
                    if not ports:
                        client.conn.sendall(self._response(cseq, {}, ""))
                        continue
                    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    rtp.bind(("", 0))
                    rtcp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    rtcp.bind(("", 0))
                    client.rtp_sock = rtp
                    client.rtcp_sock = rtcp
                    client.rtp_port = ports[0]
                    client.rtcp_port = ports[1] if len(ports) > 1 else ports[0] + 1
                    client.start_sender()
                    client.conn.sendall(
                        self._response(
                            cseq,
                            {
                                "Transport": (
                                    f"RTP/AVP;unicast;client_port={client.rtp_port}-{client.rtcp_port};"
                                    f"server_port={rtp.getsockname()[1]}-{rtcp.getsockname()[1]};"
                                    f"ssrc={client.ssrc:08X}"
                                ),
                                "Session": f"{client.session};timeout=60",
                            },
                        )
                    )
                elif method == "PLAY":
                    client.playing = True
                    client.conn.sendall(
                        self._response(cseq, {"Session": client.session, "Range": "npt=0.000-"})
                    )
                    # Give a late client the latest parameter sets/IDR immediately.
                    boot = []
                    if self._sps:
                        boot.append(self._sps)
                    if self._pps:
                        boot.append(self._pps)
                    if self._last_idr_frame:
                        for nal in self._last_idr_frame:
                            if nal[0] & 0x1F not in (7, 8):
                                boot.append(nal)
                    # Only emit a boot access unit if it actually contains a
                    # VCL slice. SPS/PPS alone makes players log "missing
                    # picture"; they are already in the SDP sprop.
                    if boot and any((nal[0] & 0x1F) in (1, 5) for nal in boot):
                        client.enqueue(boot)
                elif method in ("PAUSE",):
                    client.playing = False
                    client.conn.sendall(self._response(cseq, {"Session": client.session}))
                elif method in ("TEARDOWN",):
                    client.conn.sendall(self._response(cseq, {"Session": client.session}))
                    return
                elif method in ("GET_PARAMETER", "SET_PARAMETER"):
                    client.conn.sendall(self._response(cseq, {"Session": client.session}))
                else:
                    client.conn.sendall(self._response(cseq, {}, ""))
        except (OSError, ValueError):
            pass
        finally:
            client.close()
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
