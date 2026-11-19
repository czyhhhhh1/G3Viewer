#!/usr/bin/env python3
"""Passively read all five DJI vendor bulk interfaces through libusb0.

The goggles expose each vendor interface as a separate libusb-win32 device, so
we open them individually. No writes are issued.
"""
from __future__ import annotations

import argparse
import pathlib
import threading
import time

import usb.backend.libusb0 as libusb0
import usb.core
import usb.util

VID, PID = 0x2CA3, 0x0020


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hx:<{width * 3}} {asc}")
    return "\n".join(lines)


def enumerate_devices():
    be = libusb0.get_backend()
    found = {}
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=be):
        try:
            cfg = dev.get_active_configuration()
            interface = cfg[(0, 0)].bInterfaceNumber
        except Exception:  # noqa: BLE001
            continue
        found[interface] = dev
    return found


def reader(dev, interface: int, endpoint: int, outdir: pathlib.Path, seconds: float | None, stats: dict) -> None:
    try:
        usb.util.claim_interface(dev, interface)
    except Exception as exc:  # noqa: BLE001
        print(f"[MI{interface:02d}] claim failed: {exc}")
        stats[interface] = f"claim failed: {exc}"
        return
    path = outdir / f"mi{interface:02d}_ep{endpoint:02x}.bin"
    total = 0
    packets = 0
    started = time.monotonic()
    print(f"[MI{interface:02d}] reading EP {endpoint:#04x} -> {path}")
    first = bytearray()
    try:
        with path.open("wb") as fh:
            while seconds is None or time.monotonic() - started < seconds:
                try:
                    data = dev.read(endpoint, 256 * 1024, timeout=250)
                except usb.core.USBTimeoutError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    if "timeout" in str(exc).lower():
                        continue
                    print(f"[MI{interface:02d}] read stopped: {exc}")
                    stats[interface] = f"read stopped: {exc}"
                    break
                if not data:
                    continue
                raw = bytes(data)
                fh.write(raw)
                fh.flush()
                total += len(raw)
                packets += 1
                if len(first) < 8192:
                    first.extend(raw[: 8192 - len(first)])
                if packets <= 5:
                    print(f"[MI{interface:02d}] pkt {packets}: {len(raw)} bytes\n{hexdump(raw[:256])}")
    finally:
        try:
            usb.util.release_interface(dev, interface)
        except Exception:  # noqa: BLE001
            pass
        if first:
            (outdir / f"mi{interface:02d}_first.bin").write_bytes(first)
        if interface not in stats:
            stats[interface] = f"done: {packets} packets, {total} bytes"
        print(f"[MI{interface:02d}] done: {packets} packets, {total} bytes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0, help="0 = run until Ctrl+C")
    ap.add_argument("--out", default="captures/passive")
    args = ap.parse_args()
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    devices = enumerate_devices()
    if not devices:
        print("DJI 2ca3:0020 vendor interfaces not found. Is the goggles connected and bound to libusb-win32?")
        return 1
    print(f"Found vendor interfaces: {sorted(devices)}")

    duration = None if args.seconds == 0 else args.seconds
    stats: dict[int, str] = {}
    threads = []
    for interface, dev in sorted(devices.items()):
        cfg = dev.get_active_configuration()
        for ep in cfg[(0, 0)]:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                t = threading.Thread(
                    target=reader,
                    args=(dev, interface, ep.bEndpointAddress, outdir, duration, stats),
                    daemon=True,
                )
                t.start()
                threads.append(t)
                time.sleep(0.05)
    for t in threads:
        t.join()
    print("\nSummary:")
    for interface in sorted(stats):
        print(f"  MI{interface:02d}: {stats[interface]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
