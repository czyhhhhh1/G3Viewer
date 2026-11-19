#!/usr/bin/env python3
"""Send benign DUML Ping / Version Inquiry probes on each DJI vendor interface."""
from __future__ import annotations

import argparse
import time

import usb.backend.libusb0 as libusb0
import usb.core
import usb.util

from duml import build_duml, parse_duml

VID, PID = 0x2CA3, 0x0020


def get_backend():
    return libusb0.get_backend()


def devices():
    found = {}
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=get_backend()):
        try:
            cfg = dev.get_active_configuration()
            found[cfg[(0, 0)].bInterfaceNumber] = dev
        except Exception:  # noqa: BLE001
            continue
    return found


def endpoints(dev):
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_in = next(e.bEndpointAddress for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    ep_out = next(e.bEndpointAddress for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    return ep_in, ep_out


def drain(dev, ep_in, seconds):
    out = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            data = dev.read(ep_in, 4096, timeout=250)
        except Exception as exc:  # noqa: BLE001
            if "timeout" in str(exc).lower():
                continue
            break
        if data:
            out.append(bytes(data))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", type=int, default=4)
    ap.add_argument("--sender", type=lambda s: int(s, 0), default=0x03)
    ap.add_argument("--receiver", type=lambda s: int(s, 0), default=0xB3)
    ap.add_argument("--wait", type=float, default=1.5)
    ap.add_argument("--addr-first", action="store_true")
    args = ap.parse_args()
    devs = devices()
    if args.interface not in devs:
        print("interface not found")
        return 1
    dev = devs[args.interface]
    ep_in, ep_out = endpoints(dev)
    print(f"MI{args.interface} IN {ep_in:#x} OUT {ep_out:#x} sender {args.sender:#x} receiver {args.receiver:#x}")
    usb.util.claim_interface(dev, args.interface)
    try:
        drain(dev, ep_in, 0.3)
        for seq, payload in enumerate([b"\x00\x00", b"\x00\x01", b"\x00\x01\x00"], 1):
            pkt = build_duml(payload, args.sender, args.receiver, seq, addr_first=args.addr_first)
            print(f"TX {pkt.hex(' ')}")
            dev.write(ep_out, pkt, timeout=1000)
            replies = drain(dev, ep_in, args.wait)
            if not replies:
                print("  no reply")
            for reply in replies:
                print(f"  RX {len(reply)}: {reply.hex(' ')}")
                try:
                    print(f"  parsed: {parse_duml(reply)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  parse: {exc}")
    finally:
        usb.util.release_interface(dev, args.interface)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
