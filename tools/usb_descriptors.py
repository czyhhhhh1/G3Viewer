#!/usr/bin/env python3
"""Read strings and raw descriptors from a DJI libusb-win32 child interface."""
from __future__ import annotations

import usb.backend.libusb0 as libusb0
import usb.core

VID, PID = 0x2CA3, 0x0020


def ctrl(dev, bm, req, value, index, length):
    try:
        data = dev.ctrl_transfer(bm, req, value, index, length, timeout=1000)
        return bytes(data)
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}".encode()


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        lines.append(f"{off:04x}  " + " ".join(f"{b:02x}" for b in chunk))
    return "\n".join(lines)


def main() -> int:
    be = libusb0.get_backend()
    devices = list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=be))
    if not devices:
        print("not found")
        return 1
    dev = devices[0]
    print("device descriptor", hexdump(ctrl(dev, 0x80, 6, 0x0100, 0, 18)))
    raw_cfg = ctrl(dev, 0x80, 6, 0x0200, 0, 512)
    print("raw configuration", hexdump(raw_cfg))
    for index in range(0, 32):
        data = ctrl(dev, 0x80, 6, 0x0300 | index, 0x0409, 255)
        if data.startswith(b"ERR"):
            continue
        if len(data) >= 2 and data[1] == 3:
            try:
                text = data[2:].decode("utf-16le")
            except Exception:  # noqa: BLE001
                text = repr(data)
            print(f"string {index}: {text!r} raw={data.hex()}")
    for name, value, index, length in [
        ("BOS", 0x0F00, 0, 255),
        ("MS OS string", 0xEE00, 0, 255),
        ("device qualifier", 0x0600, 0, 10),
        ("other speed config", 0x0700, 0, 512),
    ]:
        data = ctrl(dev, 0x80, 6, value, index, length)
        print(name, data[:64].hex() if isinstance(data, bytes) else data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
