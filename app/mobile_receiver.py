"""Receive the DJI mobile/MFI logic-link video stream over USB.

Protocol reference: samuelsadok/dji_protocol, `usb_mobile_protocol.md`.
Verified there on DJI Goggles N3 (VID:PID 2ca3:1002, interface 1 alt 1).
The same transport is the documented path for DJI Goggles 3 when it has been
switched into phone/MFI mode by a USB role-switch bridge.

This module only uses libusb, no vendor DLLs. It can be unit-tested with a fake
transport (see `tools/test_mobile_parser.py`).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import usb.core
import usb.util

try:
    import libusb_package
    from usb.backend import libusb1

    def _libusb1_backend():
        path = libusb_package.get_library_path()
        return libusb1.get_backend(find_library=lambda _: path)

except Exception:  # noqa: BLE001 - optional dependency
    _libusb1_backend = lambda: None  # type: ignore[assignment]


VID = 0x2CA3
MOBILE_PID = 0x1002
PC_PID = 0x0020
VIDEO_PORT = 0x574A  # 22346
LOGICLINK_MAGIC = b"\x55\xcc"

# Two packets that start the stream. These are the Goggles N3/MFI packets from
# the public reverse-engineering notes; they contain an outer 0x55CC header, a
# DUML command and the trailing encapsulation CRC.
START_PACKETS = [
    bytes.fromhex(
        "55 cc 49 57 2d 00 00 00"
        "55 2d 04 f2 02 28 f3 fe 40 00 99"
        "02 02 00 00 d5 07 00 00 00 00 00 13 00 0d 00 63 61 6d 63 61 70 5f 63 6f 6d 6d 6f 6e 00 00 00 00 d0 93"
        "92 3a"
    ),
    bytes.fromhex(
        "55 cc 49 57 1b 00 00 00"
        "55 1b 04 75 02 3c f4 fe 40 00 88"
        "17 00 00 23 00 41 50 50 00 00 00 00 00 02 58 a6"
        "34 18"
    ),
]


@dataclass
class LinkPacket:
    port: int
    payload: bytes


class LogicLinkDecoder:
    """Reassemble 0x55CC logical-link packets split across USB transfers."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.byte_pos = 0
        self.ports: dict[int, int] = {}

    def put(self, data: bytes) -> list[LinkPacket]:
        self.buffer.extend(data)
        packets: list[LinkPacket] = []
        while len(self.buffer) >= 8:
            if self.buffer[0:2] != LOGICLINK_MAGIC:
                # Resynchronise after a damaged/partial transfer.
                idx = self.buffer.find(LOGICLINK_MAGIC, 1)
                if idx < 0:
                    self.buffer.clear()
                    break
                del self.buffer[:idx]
                self.byte_pos += idx
                continue
            length = self.buffer[4] | (self.buffer[5] << 8)
            if length > 1024 * 1024:
                del self.buffer[:2]
                self.byte_pos += 2
                continue
            if len(self.buffer) < 8 + length:
                break
            port = self.buffer[2] | (self.buffer[3] << 8)
            payload = bytes(self.buffer[8 : 8 + length])
            packets.append(LinkPacket(port=port, payload=payload))
            self.ports[port] = self.ports.get(port, 0) + 1
            del self.buffer[: 8 + length]
            self.byte_pos += 8 + length
        return packets


@dataclass
class ReceiverStats:
    connected: bool = False
    bytes_in: int = 0
    link_packets: int = 0
    video_packets: int = 0
    video_bytes: int = 0
    last_error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    ports: dict[int, int] = field(default_factory=dict)


class MobileVideoReceiver:
    """Owns the USB device and a reader thread.

    ``on_video`` is called from the reader thread with H.264 bytes.
    ``on_status`` is called with a text line for the UI log.
    """

    def __init__(
        self,
        on_video: Callable[[bytes], None],
        on_status: Callable[[str], None] | None = None,
        on_stats: Callable[[ReceiverStats], None] | None = None,
    ) -> None:
        self.on_video = on_video
        self.on_status = on_status or (lambda _msg: None)
        self.on_stats = on_stats or (lambda _stats: None)
        self.stats = ReceiverStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dev = None
        self._ep_in = None
        self._ep_out = None
        self._decoder = LogicLinkDecoder()
        self._lock = threading.Lock()
        self._last_send = 0.0
        self._backend_name = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _find_device(self):
        errors = []
        # Prefer libusb1 (WinUSB/libusbK). libusb0 is the fallback used by Zadig
        # installs on Goggles 3 PC-mode interfaces.
        for label, get_backend in (("libusb1", _libusb1_backend), ("libusb0", self._libusb0_backend)):
            backend = get_backend()
            if backend is None:
                continue
            try:
                devices = list(
                    usb.core.find(find_all=True, idVendor=VID, idProduct=MOBILE_PID, backend=backend)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
                continue
            for dev in devices:
                candidate = self._select_interface(dev)
                if candidate is not None:
                    self._backend_name = label
                    self.on_status(f"找到 {VID:04x}:{MOBILE_PID:04x}（{label}）")
                    return dev, *candidate
            errors.append(f"{label}: no interface 1 with bulk endpoints")
        raise RuntimeError("; ".join(errors) or "no DJI mobile-mode device")

    @staticmethod
    def _libusb0_backend():
        try:
            import usb.backend.libusb0 as libusb0

            return libusb0.get_backend()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _select_interface(dev):
        try:
            cfg = dev.get_active_configuration()
        except Exception:  # noqa: BLE001
            return None
        for intf in cfg:
            if intf.bInterfaceNumber != 1:
                continue
            # alt 0 usually has no endpoints; take whichever alt has both.
            for alt in cfg:
                if alt.bInterfaceNumber != 1:
                    continue
                if alt.bNumEndpoints < 2:
                    continue
                ep_in = ep_out = None
                for ep in alt:
                    if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                        ep_in = ep
                    else:
                        ep_out = ep
                if ep_in and ep_out:
                    return alt.bAlternateSetting, ep_in.bEndpointAddress, ep_out.bEndpointAddress
        return None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.stats = ReceiverStats()
        self._decoder = LogicLinkDecoder()
        self._thread = threading.Thread(target=self._run, name="dji-mobile-usb", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._close_device()
        self.stats.connected = False
        self.on_status("已断开")

    def _close_device(self) -> None:
        if self._dev is not None:
            try:
                usb.util.dispose_resources(self._dev)
            except Exception:  # noqa: BLE001
                pass
            self._dev = None
            self._ep_in = None
            self._ep_out = None

    def _open(self) -> None:
        dev, alt, ep_in, ep_out = self._find_device()
        self.on_status(
            f"接口 1 alt {alt}，IN {ep_in:#04x}，OUT {ep_out:#04x}，后端 {self._backend_name}"
        )
        try:
            usb.util.claim_interface(dev, 1)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"claim interface 1 failed: {exc}") from exc
        try:
            dev.set_interface_altsetting(interface=1, alternate_setting=alt)
        except Exception as exc:  # noqa: BLE001
            self.on_status(f"设置 alt {alt} 失败（设备可能已在 alt {alt}）：{exc}")
        self._dev, self._ep_in, self._ep_out = dev, ep_in, ep_out
        self.stats.connected = True

    def _send_start(self) -> None:
        if self._dev is None or self._ep_out is None:
            return
        for packet in START_PACKETS:
            self._dev.write(self._ep_out, packet, timeout=1000)
        self._last_send = time.monotonic()
        self.on_status(f"已发送开流命令（{len(START_PACKETS)} 包）")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._open()
                self._send_start()
                while not self._stop.is_set():
                    try:
                        raw = self._dev.read(self._ep_in, 256 * 1024, timeout=500)
                    except usb.core.USBTimeoutError:
                        raw = None
                    except Exception as exc:  # noqa: BLE001
                        if "timeout" in str(exc).lower():
                            raw = None
                        else:
                            raise
                    if raw:
                        self._handle(bytes(raw))
                    if time.monotonic() - self._last_send > 5.0:
                        self._send_start()
                    self.on_stats(self.stats)
            except Exception as exc:  # noqa: BLE001
                self.stats.last_error = str(exc)
                self.stats.connected = False
                self.on_status(f"USB 错误：{exc}")
                self._close_device()
                if self._stop.wait(1.5):
                    break
        self._close_device()

    def _handle(self, raw: bytes) -> None:
        with self._lock:
            self.stats.bytes_in += len(raw)
            packets = self._decoder.put(raw)
            self.stats.link_packets += len(packets)
            for packet in packets:
                self.stats.ports = dict(self._decoder.ports)
                if packet.port == VIDEO_PORT:
                    self.stats.video_packets += 1
                    self.stats.video_bytes += len(packet.payload)
                    self.on_video(packet.payload)
        self.on_stats(self.stats)


def probe_mobile_mode() -> str:
    """Return a human-readable description, mainly for the diagnostics tab."""
    dev = None
    try:
        receiver = MobileVideoReceiver(lambda _data: None)
        dev, alt, ep_in, ep_out = receiver._find_device()
        return (
            f"手机模式设备 {VID:04x}:{MOBILE_PID:04x} 可用；"
            f"interface 1 alt {alt}，IN {ep_in:#04x}，OUT {ep_out:#04x}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"未发现手机模式设备：{exc}"
    finally:
        if dev is not None:
            try:
                usb.util.dispose_resources(dev)
            except Exception:  # noqa: BLE001
                pass
