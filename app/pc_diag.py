"""Read-only diagnostics for the DJI Goggles 3 PC/OTG mode.

The PC mode exposes no UVC interface. This module proves that from the live
device and decodes the MI04 DUML status heartbeat so the UI can show something
useful instead of guessing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import usb.core
import usb.util

try:
    import usb.backend.libusb0 as libusb0
except Exception:  # noqa: BLE001
    libusb0 = None

VID = 0x2CA3
PC_PID = 0x0020
MOBILE_PID = 0x1002

INTERFACE_ROLES = {
    0: "RNDIS 控制",
    1: "RNDIS/CDC 数据",
    2: "Mass Storage（U 盘）",
    3: "BULK 通道 3",
    4: "BULK 通道 4（DUML 状态）",
    5: "BULK 通道 5",
    6: "BULK 通道 6",
    7: "BULK 通道 7",
}


@dataclass
class Heartbeat:
    length: int
    version: int
    cmd_set: int
    cmd_id: int
    ascii_hint: str
    raw_hex: str


def _crc8_header(data: bytes, seed: int = 0x77) -> int:
    table = [
        0x00, 0x5E, 0xBC, 0xE2, 0x61, 0x3F, 0xDD, 0x83, 0xC2, 0x9C, 0x7E, 0x20, 0xA3, 0xFD, 0x1F, 0x41,
        0x9D, 0xC3, 0x21, 0x7F, 0xFC, 0xA2, 0x40, 0x1E, 0x5F, 0x01, 0xE3, 0xBD, 0x3E, 0x60, 0x82, 0xDC,
        0x23, 0x7D, 0x9F, 0xC1, 0x42, 0x1C, 0xFE, 0xA0, 0xE1, 0xBF, 0x5D, 0x03, 0x80, 0xDE, 0x3C, 0x62,
        0xBE, 0xE0, 0x02, 0x5C, 0xDF, 0x81, 0x63, 0x3D, 0x7C, 0x22, 0xC0, 0x9E, 0x1D, 0x43, 0xA1, 0xFF,
        0x46, 0x18, 0xFA, 0xA4, 0x27, 0x79, 0x9B, 0xC5, 0x84, 0xDA, 0x38, 0x66, 0xE5, 0xBB, 0x59, 0x07,
        0xDB, 0x85, 0x67, 0x39, 0xBA, 0xE4, 0x06, 0x58, 0x19, 0x47, 0xA5, 0xFB, 0x78, 0x26, 0xC4, 0x9A,
        0x65, 0x3B, 0xD9, 0x87, 0x04, 0x5A, 0xB8, 0xE6, 0xA7, 0xF9, 0x1B, 0x45, 0xC6, 0x98, 0x7A, 0x24,
        0xF8, 0xA6, 0x44, 0x1A, 0x99, 0xC7, 0x25, 0x7B, 0x3A, 0x64, 0x86, 0xD8, 0x5B, 0x05, 0xE7, 0xB9,
        0x8C, 0xD2, 0x30, 0x6E, 0xED, 0xB3, 0x51, 0x0F, 0x4E, 0x10, 0xF2, 0xAC, 0x2F, 0x71, 0x93, 0xCD,
        0x11, 0x4F, 0xAD, 0xF3, 0x70, 0x2E, 0xCC, 0x92, 0xD3, 0x8D, 0x6F, 0x31, 0xB2, 0xEC, 0x0E, 0x50,
        0xAF, 0xF1, 0x13, 0x4D, 0xCE, 0x90, 0x72, 0x2C, 0x6D, 0x33, 0xD1, 0x8F, 0x0C, 0x52, 0xB0, 0xEE,
        0x32, 0x6C, 0x8E, 0xD0, 0x53, 0x0D, 0xEF, 0xB1, 0xF0, 0xAE, 0x4C, 0x12, 0x91, 0xCF, 0x2D, 0x73,
        0xCA, 0x94, 0x76, 0x28, 0xAB, 0xF5, 0x17, 0x49, 0x08, 0x56, 0xB4, 0xEA, 0x69, 0x37, 0xD5, 0x8B,
        0x57, 0x09, 0xEB, 0xB5, 0x36, 0x68, 0x8A, 0xD4, 0x95, 0xCB, 0x29, 0x77, 0xF4, 0xAA, 0x48, 0x16,
        0xE9, 0xB7, 0x55, 0x0B, 0x88, 0xD6, 0x34, 0x6A, 0x2B, 0x75, 0x97, 0xC9, 0x4A, 0x14, 0xF6, 0xA8,
        0x74, 0x2A, 0xC8, 0x96, 0x15, 0x4B, 0xA9, 0xF7, 0xB6, 0xE8, 0x0A, 0x54, 0xD7, 0x89, 0x6B, 0x35,
    ]
    chk = seed
    for byte in data:
        chk = table[(byte ^ chk) & 0xFF]
    return chk


def parse_heartbeat(raw: bytes) -> Heartbeat | None:
    if len(raw) < 12 or raw[0] != 0x55:
        return None
    total = raw[1]
    if total > len(raw) or total < 12:
        return None
    packet = raw[:total]
    if packet[2] != 0x04 or _crc8_header(packet[:3]) != packet[3]:
        return None
    payload = packet[8:-2]
    if len(payload) < 3:
        return None
    ascii_hint = "".join(chr(b) if 32 <= b < 127 else "." for b in payload[3:15])
    return Heartbeat(
        length=total,
        version=packet[2],
        cmd_set=payload[0],
        cmd_id=(payload[1] << 8) | payload[2],
        ascii_hint=ascii_hint,
        raw_hex=packet.hex(" "),
    )


def _libusb0_devices():
    if libusb0 is None:
        return {}
    backend = libusb0.get_backend()
    found = {}
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PC_PID, backend=backend):
        try:
            cfg = dev.get_active_configuration()
            found[cfg[(0, 0)].bInterfaceNumber] = dev
        except Exception:  # noqa: BLE001
            continue
    return found


def _all_interfaces() -> list[tuple[int, int, int, int, list[int]]]:
    """Read the composite parent descriptor with libusb1, without claiming it."""
    try:
        import libusb_package
        from usb.backend import libusb1

        path = libusb_package.get_library_path()
        backend = libusb1.get_backend(find_library=lambda _: path)
        dev = usb.core.find(idVendor=VID, idProduct=PC_PID, backend=backend)
        if dev is None:
            return []
        result = []
        for cfg in dev:
            for intf in cfg:
                eps = [ep.bEndpointAddress for ep in intf]
                result.append(
                    (
                        intf.bInterfaceNumber,
                        intf.bInterfaceClass,
                        intf.bInterfaceSubClass,
                        intf.bInterfaceProtocol,
                        eps,
                    )
                )
        return result
    except Exception:  # noqa: BLE001
        return []


def read_heartbeats(interface: int = 4, seconds: float = 3.0) -> tuple[list[Heartbeat], str]:
    devices = _libusb0_devices()
    dev = devices.get(interface)
    if dev is None:
        return [], f"未找到接口 {interface}；设备可能未连接或驱动不是 libusb-win32"
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    try:
        ep_in = next(
            ep.bEndpointAddress
            for ep in intf
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
    except StopIteration:
        return [], f"接口 {interface} 没有 IN 端点"
    heartbeats: list[Heartbeat] = []
    try:
        usb.util.claim_interface(dev, interface)
    except Exception as exc:  # noqa: BLE001
        return [], f"claim 接口 {interface} 失败：{exc}"
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            try:
                raw = dev.read(ep_in, 4096, timeout=300)
            except Exception as exc:  # noqa: BLE001
                if "timeout" in str(exc).lower():
                    continue
                return heartbeats, f"读取失败：{exc}"
            if not raw:
                continue
            heartbeat = parse_heartbeat(bytes(raw))
            if heartbeat:
                heartbeats.append(heartbeat)
    finally:
        try:
            usb.util.release_interface(dev, interface)
        except Exception:  # noqa: BLE001
            pass
    return heartbeats, ""


def read_string(dev, index: int) -> str:
    try:
        data = dev.ctrl_transfer(0x80, 6, 0x0300 | index, 0x0409, 255, timeout=500)
        if len(data) >= 2 and data[1] == 3:
            return bytes(data[2:]).decode("utf-16le", "replace")
    except Exception:  # noqa: BLE001
        pass
    return ""


def describe_pc_mode() -> str:
    lines = [f"扫描 DJI {VID:04x}:{PC_PID:04x}（PC/OTG 模式）..."]
    devices = _libusb0_devices()
    if not devices:
        lines.append("未找到 PC 模式设备。")
        lines.append("请确认眼镜已开机、USB-C 已连接，并在眼镜菜单进入“设置 > 关于 > OTG 有线连接模式”。")
        return "\n".join(lines)

    first = devices[min(devices)]
    strings = {i: read_string(first, i) for i in range(1, 16)}
    lines.append(f"设备：{strings.get(1, '?')} {strings.get(2, '?')}  SN {strings.get(3, '?')}")
    lines.append(f"配置功能串：{strings.get(4, '?')}")
    lines.append("")
    lines.append("接口：")
    full = _all_interfaces()
    if full:
        for number, cls, sub, proto, eps in sorted(full):
            ep_text = ", ".join(
                f"{'IN' if ep & 0x80 else 'OUT'} {ep:#04x}" for ep in eps
            )
            lines.append(
                f"  MI{number:02d}  class={cls:#04x}/{sub:#04x}/{proto:#04x}  "
                f"{INTERFACE_ROLES.get(number, '未知')}  [{ep_text}]"
            )
        lines.append("  （以上为完整复合设备描述符；厂商 Bulk 通道通过 libusb-win32 访问）")
    else:
        for number in sorted(devices):
            dev = devices[number]
            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]
            eps = ", ".join(
                f"{'IN' if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN else 'OUT'}"
                f" {e.bEndpointAddress:#04x}"
                for e in intf
            )
            lines.append(
                f"  MI{number:02d}  class={intf.bInterfaceClass:#04x}/"
                f"{intf.bInterfaceSubClass:#04x}/{intf.bInterfaceProtocol:#04x}  "
                f"{INTERFACE_ROLES.get(number, '未知')}  [{eps}]"
            )
    lines.append("")
    lines.append("UVC 摄像头接口：未发现（PC 模式没有 class 0x0e 的视频接口）")
    lines.append("结论：PC/OTG 模式仅提供网络、存储、Bulk 状态和串口，不能直接当摄像头输出。")
    lines.append("")
    beats, error = read_heartbeats(4, 3.0)
    if error:
        lines.append(f"MI04 心跳：{error}")
    elif beats:
        beat = beats[-1]
        lines.append(
            f"MI04 心跳：{len(beats)} 包/3s，DUML {beat.length} 字节，"
            f"cmd_set={beat.cmd_set:#04x} cmd_id=0x{beat.cmd_id:04x}，标识={beat.ascii_hint!r}"
        )
        lines.append(f"  最后一包：{beat.raw_hex[:120]}...")
    else:
        lines.append("MI04 心跳：3 秒内没有收到数据。")
    lines.append("")
    lines.append("手机模式：请先用 USB 角色切换桥把眼镜切到 2ca3:1002 后再点“手机模式 USB 收流”。")
    return "\n".join(lines)
