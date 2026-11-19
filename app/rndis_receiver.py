"""DJI Goggles 3 LiveView over USB RNDIS.

This is the missing PC path: the goggles expose a Microsoft RNDIS adapter, and
when "LiveView sharing" is enabled they send H.264 over UDP to
192.168.60.2:9003. The host side needs 192.168.60.1/24 on that adapter.

Protocol implementation follows the MIT-licensed Goggle 3 LiveView project
(Windows/Goggles3.Core/LiveProtocol.cs) and was independently reimplemented in
Python. The handshake, session id, XOR header byte, frame reassembly and ack
layout are kept byte-compatible.
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

HOST_IP = "192.168.60.1"
PEER_IP = "192.168.60.2"
PEER_PORT = 9003
LOCAL_PORT = 12346
PREFIX = 24


def is_idr(data: bytes) -> bool:
    head = data[:96]
    return b"\x00\x00\x00\x01\x65" in head or b"\x00\x00\x01\x65" in head


def header_xor(data: bytes | bytearray) -> int:
    value = 0
    for byte in data[:7]:
        value ^= byte
    return value


def create_handshake(session: int) -> bytes:
    data = bytearray([
        0x30, 0x80, 0, 0, 0, 0, 0, 0, 0xD0, 0xE9, 0x64, 0, 0x64, 0, 0xC0, 0x05,
        0x14, 0, 0, 0x0A, 0, 0x64, 0, 0x64, 0, 0xC0, 0x05, 0x14, 0, 0, 0x64, 0,
        0x14, 0, 0x64, 0, 0xC0, 0x05, 0x14, 0, 0, 0x64, 0, 1, 1, 4, 0x0A, 2,
    ])
    struct.pack_into("<H", data, 2, session & 0xFFFF)
    data[7] = header_xor(data)
    return bytes(data)


def create_ack(session: int, sequence: int) -> bytes:
    data = bytearray([
        0x1E, 0x80, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0xD0, 0xE9,
        0xD0, 0xE9, 0, 0, 0xD0, 0xE9, 0xD8, 0xE9, 0, 0, 0, 0, 0, 0,
    ])
    struct.pack_into("<H", data, 2, session & 0xFFFF)
    struct.pack_into("<H", data, 8, sequence & 0xFFFF)
    struct.pack_into("<H", data, 10, sequence & 0xFFFF)
    data[7] = header_xor(data)
    return bytes(data)


@dataclass
class PacketResult:
    video: bytes | None = None
    acknowledgement: bytes | None = None
    handshake_confirmed: bool = False


class LiveProtocol:
    """Frame reassembly for the LiveView UDP stream."""

    def __init__(self, session: int) -> None:
        self.session = session
        self.units: dict[int, dict] = {}
        self.order: list[int] = []
        self.video_bytes = 0
        self.gaps = 0
        self._last_sequence: int | None = None

    def accept(self, message: bytes) -> PacketResult:
        if (
            len(message) < 8
            or (struct.unpack_from("<H", message, 0)[0] & 0x7FFF) != len(message)
            or struct.unpack_from("<H", message, 2)[0] != self.session
            or message[7] != header_xor(message)
        ):
            return PacketResult()
        if message[6] == 0:
            return PacketResult(handshake_confirmed=True)
        if message[6] != 2 or len(message) < 20:
            return PacketResult()
        count = message[17] & 0x7F
        part = (message[17] >> 7) | (message[18] << 1) | (message[19] << 9)
        if count == 0 or part >= count:
            return PacketResult()
        sequence = struct.unpack_from("<H", message, 4)[0]
        if self._last_sequence is not None:
            step = (sequence - self._last_sequence) & 0xFFFF
            if 8 < step < 0x8000 and step % 8 == 0:
                self.gaps += step // 8 - 1
        self._last_sequence = sequence
        key = ((sequence & 0xFFF8) - part * 8) & 0xFFFF
        unit = self.units.get(key)
        if unit is None:
            if len(self.units) >= 32:
                old = self.order.pop(0)
                self.units.pop(old, None)
            unit = {"parts": [None] * count, "received": 0, "complete": False}
            self.units[key] = unit
            self.order.append(key)
        if len(unit["parts"]) != count:
            return PacketResult()
        ack = create_ack(self.session, sequence) if part + 1 == count else None
        if unit["complete"] or unit["parts"][part] is not None:
            return PacketResult(acknowledgement=ack)
        unit["parts"][part] = message[20:]
        self.video_bytes += len(message) - 20
        unit["received"] += 1
        if unit["received"] != count:
            return PacketResult(acknowledgement=ack)
        frame = b"".join(unit["parts"])
        unit["complete"] = True
        unit["parts"] = [None] * count
        return PacketResult(video=frame, acknowledgement=ack)


@dataclass
class RndisStats:
    connected: bool = False
    handshake_confirmed: bool = False
    video_frames: int = 0
    video_bytes: int = 0
    datagrams: int = 0
    keyframes: int = 0
    gaps: int = 0
    last_error: str = ""
    started_at: float = field(default_factory=time.monotonic)


def _run_hidden(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=flags,
    )


def check_rndis_ip() -> bool:
    """Return True if 192.168.60.1/24 is already on a DJI RNDIS adapter."""
    script = (
        "$a=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object { $_.IPAddress -eq '192.168.60.1' -and $_.PrefixLength -eq 24 }); "
        "if($a.Count -gt 0){'YES'}else{'NO'}"
    )
    try:
        result = _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=20)
        return "YES" in result.stdout
    except Exception:  # noqa: BLE001
        return False


PREPARE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $devices = @(Get-CimInstance Win32_NetworkAdapter | Where-Object {
        $_.PNPDeviceID -match '^USB\\VID_2CA3&PID_0020(?:&|\\)'
    })
    if ($devices.Count -ne 1) { throw 'Exactly one DJI Goggles 3 RNDIS adapter is required.' }
    $device = $devices[0]
    $adapter = Get-NetAdapter -IncludeHidden | Where-Object {
        $_.InterfaceGuid.ToString().Trim('{}') -eq $device.GUID.Trim('{}')
    }
    if (@($adapter).Count -ne 1) { throw 'The RNDIS driver has not created a usable adapter.' }
    $index = $adapter.ifIndex
    $addresses = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    if (@($addresses | Where-Object { $_.IPAddress -eq '192.168.60.1' -and $_.PrefixLength -eq 24 }).Count -gt 0) { exit 0 }
    if (@($addresses | Where-Object { $_.IPAddress -ne '192.168.60.1' -and $_.IPAddress -notlike '169.254.*' }).Count -gt 0) {
        throw 'The goggles adapter already has a different static address. It was not changed.'
    }
    $conflict = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq '192.168.60.1' -and $_.InterfaceIndex -ne $index }
    if ($conflict) { throw '192.168.60.1 already belongs to another adapter.' }
    New-NetIPAddress -InterfaceIndex $index -IPAddress '192.168.60.1' -PrefixLength 24 -AddressFamily IPv4 | Out-Null
    exit 0
} catch {
    exit 1
}
"""


def prepare_rndis_ip(log: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Add 192.168.60.1/24 to the goggles adapter via one UAC prompt.

    Returns (ok, message). The helper only touches the adapter whose PNP ID is
    USB\\VID_2CA3&PID_0020*; it does not change the gateway, DNS or firewall.
    """
    log = log or (lambda _m: None)
    if check_rndis_ip():
        return True, "RNDIS 地址已配置"
    log("需要管理员权限给眼镜 RNDIS 网卡添加 192.168.60.1/24（只改这个网卡，不改网关/DNS/防火墙）。")
    encoded = __import__("base64").b64encode(PREPARE_SCRIPT.encode("utf-16le")).decode("ascii")
    outer = (
        "$ErrorActionPreference='Stop';"
        f"$p=Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru "
        f"-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{encoded}');"
        "exit $p.ExitCode"
    )
    try:
        result = _run_hidden(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", outer], timeout=180)
    except subprocess.TimeoutExpired:
        return False, "等待 UAC/配置超时"
    if result.returncode != 0:
        return False, "UAC 被取消，或网卡配置失败（需要管理员权限）"
    if check_rndis_ip():
        return True, "RNDIS 地址 192.168.60.1/24 已配置"
    return False, "配置命令返回成功，但未检测到 192.168.60.1"


class RndisVideoReceiver:
    """Receive LiveView H.264 over the goggles USB RNDIS adapter."""

    def __init__(
        self,
        on_video: Callable[[bytes], None],
        on_status: Callable[[str], None] | None = None,
        on_stats: Callable[[RndisStats], None] | None = None,
    ) -> None:
        self.on_video = on_video
        self.on_status = on_status or (lambda _m: None)
        self.on_stats = on_stats or (lambda _s: None)
        self.stats = RndisStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._session = int.from_bytes(os.urandom(2), "little")
        self._protocol = LiveProtocol(self._session)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dji-rndis", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self.stats.connected = False
        self.on_status("RNDIS 已断开")

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            sock.bind((HOST_IP, LOCAL_PORT))
            sock.connect((PEER_IP, PEER_PORT))
            sock.settimeout(0.25)
            self._sock = sock
            self.on_status(f"等待 LiveView：{HOST_IP}:{LOCAL_PORT} → {PEER_IP}:{PEER_PORT}")
        except OSError as exc:
            self.stats.last_error = str(exc)
            self.on_status(
                f"绑定 {HOST_IP}:{LOCAL_PORT} 失败：{exc}。请先允许程序配置 RNDIS 网卡，"
                "并在眼镜里开启 OTG 有线连接 + LiveView sharing。"
            )
            return

        handshake = create_handshake(self._session)
        last_send = 0.0
        started = False
        saw_keyframe = False
        start_time = time.monotonic()
        warned_no_reply = False
        warned_no_video = False
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                # Keep the session subscribed. Some firmware stops the UDP
                # stream if the handshake is not refreshed, even after an IDR.
                if now - last_send >= 2.0:
                    try:
                        sock.send(handshake)
                    except OSError as exc:
                        self.stats.last_error = str(exc)
                        self.on_status(f"发送 LiveView 握手失败：{exc}")
                    last_send = now
                if not self.stats.handshake_confirmed and not warned_no_reply and now - start_time > 8:
                    warned_no_reply = True
                    self.on_status(
                        "8 秒没有收到 LiveView 响应：请确认眼镜里已打开 LiveView sharing，"
                        "并重新拔插 USB。只有 OTG 文件模式不会回视频。"
                    )
                if self.stats.handshake_confirmed and not saw_keyframe and not warned_no_video and now - start_time > 12:
                    warned_no_video = True
                    self.on_status(
                        "收到视频但 12 秒没有 IDR 关键帧。请在眼镜上把 LiveView 共享关掉再打开，"
                        "保持本程序连接；程序会继续握手等待关键帧。"
                    )
                try:
                    data = sock.recv(65535)
                except socket.timeout:
                    self.on_stats(self.stats)
                    continue
                except OSError:
                    break
                if not data:
                    continue
                self.stats.datagrams += 1
                result = self._protocol.accept(data)
                self.stats.gaps = self._protocol.gaps
                if result.handshake_confirmed:
                    self.stats.handshake_confirmed = True
                    self.stats.connected = True
                    self.on_status("LiveView 会话已确认，等待视频帧...")
                if result.acknowledgement:
                    try:
                        sock.send(result.acknowledgement)
                    except OSError:
                        pass
                if result.video:
                    started = True
                    self.stats.connected = True
                    self.stats.video_frames += 1
                    self.stats.video_bytes += len(result.video)
                    if is_idr(result.video):
                        self.stats.keyframes += 1
                        if not saw_keyframe:
                            saw_keyframe = True
                            self.on_status("收到 IDR 关键帧，开始解码。")
                    self.on_video(result.video)
                self.on_stats(self.stats)
        except Exception as exc:  # noqa: BLE001
            self.stats.last_error = str(exc)
            self.on_status(f"RNDIS 接收错误：{exc}")
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
            self._sock = None
            self.stats.connected = False
            self.on_status("RNDIS 接收已停止")


def probe_rndis() -> str:
    available = check_rndis_ip()
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        test.bind((HOST_IP, 0))
        test.close()
        bind_ok = True
    except OSError as exc:
        bind_ok = False
        bind_error = str(exc)
    lines = [
        f"RNDIS 网卡地址 192.168.60.1/24：{'已配置' if available else '未配置'}",
        f"本地绑定测试：{'成功' if bind_ok else '失败：' + bind_error}",
        f"目标：{PEER_IP}:{PEER_PORT}，本地端口 {LOCAL_PORT}",
    ]
    if not available:
        lines.append("连接时程序会请求一次 UAC，只给 VID 2CA3:PID 0020 的网卡加地址。")
    lines.append("前提：眼镜已开启 OTG 有线连接 + LiveView sharing，并已连接天空端。")
    return "\n".join(lines)
