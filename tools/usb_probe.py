#!/usr/bin/env python3
"""Enumerate a DJI USB device and dump its descriptors and driver-bound interfaces.

Usage:
    python tools/usb_probe.py            # auto-detect DJI 2ca3:0020
    python tools/usb_probe.py 2ca3 0020
"""
from __future__ import annotations

import sys
from typing import Iterable

import libusb_package
import usb.core
import usb.util
from usb.backend import libusb1


def backend():
    path = libusb_package.get_library_path()
    return libusb1.get_backend(find_library=lambda _: path)


def safe(value, fn):
    try:
        return fn(value)
    except Exception as exc:  # noqa: BLE001 - descriptor reads can fail per field
        return f"<{type(exc).__name__}: {exc}>"


def endpoint_type(attr: int) -> str:
    return {0: "CONTROL", 1: "ISO", 2: "BULK", 3: "INTERRUPT"}.get(attr & 0x03, "?")


def dump(dev: usb.core.Device) -> None:
    print("=" * 78)
    print(
        f"Device {dev.idVendor:04x}:{dev.idProduct:04x} "
        f"bus={dev.bus} addr={dev.address} speed={getattr(dev, 'speed', '?')}"
    )
    print(f"  class/subclass/protocol: {dev.bDeviceClass}/{dev.bDeviceSubClass}/{dev.bDeviceProtocol}")
    print(f"  bcdUSB={dev.bcdUSB:#06x} bcdDevice={dev.bcdDevice:#06x}")
    for name in ("manufacturer", "product", "serial_number"):
        print(f"  {name}: {safe(dev, lambda d, n=name: getattr(d, n))}")

    for cfg in dev:
        print(
            f"  Configuration {cfg.bConfigurationValue}: "
            f"{safe(cfg, lambda c: c.configuration)} "
            f"self_powered={cfg.bmAttributes & 0x40 != 0} "
            f"max_power={cfg.bMaxPower * 2}mA interfaces={cfg.bNumInterfaces}"
        )
        for intf in cfg:
            alt = intf
            print(
                f"    Interface {alt.bInterfaceNumber}.{alt.bAlternateSetting}: "
                f"class={alt.bInterfaceClass:#04x}/{alt.bInterfaceSubClass:#04x}/"
                f"{alt.bInterfaceProtocol:#04x} "
                f"eps={alt.bNumEndpoints} driver={safe(dev, lambda d, n=alt.bInterfaceNumber: usb.util.get_string(d, 0) if False else '')}"
            )
            for ep in alt:
                print(
                    f"      EP {ep.bEndpointAddress:#04x} {endpoint_type(ep.bmAttributes):9s} "
                    f"maxPacket={ep.wMaxPacketSize} interval={ep.bInterval} "
                    f"refresh={getattr(ep, 'bRefresh', 0)} synch={getattr(ep, 'bSynchAddress', 0)}"
                )
    print("=" * 78)


def find_devices(vid: int, pid: int, be) -> Iterable[usb.core.Device]:
    yield from usb.core.find(find_all=True, idVendor=vid, idProduct=pid, backend=be)


def main() -> int:
    vid, pid = 0x2CA3, 0x0020
    if len(sys.argv) == 3:
        vid, pid = int(sys.argv[1], 16), int(sys.argv[2], 16)
    be = backend()
    found = list(find_devices(vid, pid, be))
    if not found:
        print(f"No USB device {vid:04x}:{pid:04x} found.", file=sys.stderr)
        return 1
    for dev in found:
        dump(dev)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
