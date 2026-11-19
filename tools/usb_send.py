#!/usr/bin/env python3
"""Controlled bulk write probe for DJI vendor interfaces.

Reads continuously, writes one short payload to the OUT endpoint, and stores
everything received with timestamps. Only vendor interfaces 3..7 are touched.
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


def get_interface_device(interface: int):
    be = libusb0.get_backend()
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=be):
        try:
            cfg = dev.get_active_configuration()
            if cfg[(0, 0)].bInterfaceNumber == interface:
                return dev
        except Exception:  # noqa: BLE001
            continue
    return None


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hx:<{width * 3}} {asc}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", type=int, default=4)
    ap.add_argument("--payload", default="524d5654", help="hex bytes to write")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default="captures/probe")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = bytes.fromhex(args.payload)
    if len(payload) > 4096:
        print("refusing payload > 4096 bytes")
        return 2
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    dev = get_interface_device(args.interface)
    if dev is None:
        print(f"interface {args.interface} not found")
        return 1
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_in = next(ep.bEndpointAddress for ep in intf if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN)
    ep_out = next(ep.bEndpointAddress for ep in intf if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    print(f"MI{args.interface:02d} EP OUT {ep_out:#04x} EP IN {ep_in:#04x}")
    print(f"payload ({len(payload)}): {payload.hex(' ')}")
    if args.dry_run:
        return 0

    usb.util.claim_interface(dev, args.interface)
    stop = threading.Event()
    records = []

    def reader():
        while not stop.is_set():
            try:
                data = dev.read(ep_in, 256 * 1024, timeout=250)
            except Exception as exc:  # noqa: BLE001
                if "timeout" in str(exc).lower():
                    continue
                print(f"read error: {exc}")
                break
            if data:
                records.append((time.monotonic(), bytes(data)))
                print(f"RX {len(data)} bytes @{records[-1][0]:.3f}\n{hexdump(bytes(data[:256]))}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.5)
    try:
        written = dev.write(ep_out, payload, timeout=2000)
        print(f"TX {written} bytes")
    except Exception as exc:  # noqa: BLE001
        print(f"write error: {exc}")
    time.sleep(args.seconds)
    stop.set()
    t.join(timeout=2)
    usb.util.release_interface(dev, args.interface)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = outdir / f"probe_mi{args.interface:02d}_{stamp}.bin"
    with raw.open("wb") as fh:
        for ts, data in records:
            fh.write(f"@{ts:.6f} len={len(data)}\n".encode())
            fh.write(data)
    print(f"saved {raw} ({sum(len(d) for _, d in records)} RX bytes in {len(records)} packets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
