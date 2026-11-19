#!/usr/bin/env python3
"""Passively read every vendor-specific bulk IN endpoint of the DJI device.

No data is written to the device. Each endpoint gets its own reader thread and
raw capture file. Useful to see whether the goggles already stream video.
"""
from __future__ import annotations

import argparse
import pathlib
import threading
import time

import libusb_package
import usb.core
import usb.util
from usb.backend import libusb1

VID, PID = 0x2CA3, 0x0020
VENDOR_IFACES = (3, 4, 5, 6, 7)


def get_backend():
    path = libusb_package.get_library_path()
    return libusb1.get_backend(find_library=lambda _: path)


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hx:<{width * 3}} {asc}")
    return "\n".join(lines)


def reader(dev, interface: int, endpoint: int, outdir: pathlib.Path, seconds: float | None, stats: dict) -> None:
    try:
        usb.util.claim_interface(dev, interface)
    except Exception as exc:  # noqa: BLE001
        stats[interface] = f"claim failed: {exc}"
        print(f"[MI{interface:02d}] claim failed: {exc}")
        return
    path = outdir / f"mi{interface:02d}_ep{endpoint:02x}.bin"
    total = 0
    packets = 0
    started = time.monotonic()
    first = bytearray()
    print(f"[MI{interface:02d}] reading EP {endpoint:#04x} -> {path}")
    try:
        with path.open("wb") as fh:
            while seconds is None or time.monotonic() - started < seconds:
                try:
                    data = dev.read(endpoint, 256 * 1024, timeout=500)
                except usb.core.USBTimeoutError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    print(f"[MI{interface:02d}] read stopped: {exc}")
                    break
                if not data:
                    continue
                raw = bytes(data)
                fh.write(raw)
                fh.flush()
                total += len(raw)
                packets += 1
                if len(first) < 4096:
                    first.extend(raw[: 4096 - len(first)])
                if packets <= 8:
                    print(f"[MI{interface:02d}] pkt {packets}: {len(raw)} bytes\n{hexdump(raw[:256])}")
    finally:
        try:
            usb.util.release_interface(dev, interface)
        except Exception:  # noqa: BLE001
            pass
        stats[interface] = f"{packets} packets, {total} bytes"
        print(f"[MI{interface:02d}] done: {packets} packets, {total} bytes")
        if first:
            (outdir / f"mi{interface:02d}_first.bin").write_bytes(first)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0, help="capture duration per endpoint (0 = forever)")
    ap.add_argument("--out", default="captures/passive")
    args = ap.parse_args()
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=get_backend())
    if dev is None:
        print("DJI 2ca3:0020 not found")
        return 1

    # Read the active configuration to discover endpoints without opening child devices.
    cfg = dev.get_active_configuration()
    jobs = []
    for intf in cfg:
        if intf.bInterfaceNumber not in VENDOR_IFACES:
            continue
        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                jobs.append((intf.bInterfaceNumber, ep.bEndpointAddress))

    stats: dict[int, str] = {}
    threads = []
    duration = None if args.seconds == 0 else args.seconds
    for interface, ep in jobs:
        t = threading.Thread(target=reader, args=(dev, interface, ep, outdir, duration, stats), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.1)
    for t in threads:
        t.join()
    print("\nSummary:")
    for interface in sorted(stats):
        print(f"  MI{interface:02d}: {stats[interface]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
