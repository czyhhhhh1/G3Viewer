#!/usr/bin/env python3
"""Find the bidirectional DUML control channel among DJI vendor interfaces."""
from __future__ import annotations

import time

import usb.backend.libusb0 as libusb0
import usb.core
import usb.util

from duml import build_duml, parse_duml

VID, PID = 0x2CA3, 0x0020
ADDRESS_PAIRS = [(0x2A, 0xBC), (0xBC, 0x2A), (0x03, 0xB3), (0xB3, 0x03), (0xF3, 0xFE), (0xFE, 0xF3)]
VARIANTS = [False, True]


def get_backend():
    return libusb0.get_backend()


def get_interfaces():
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


def main() -> int:
    devs = get_interfaces()
    print(f"interfaces {sorted(devs)}")
    for interface in sorted(devs):
        if interface < 3:
            continue
        dev = devs[interface]
        ep_in, ep_out = endpoints(dev)
        try:
            usb.util.claim_interface(dev, interface)
        except Exception as exc:  # noqa: BLE001
            print(f"MI{interface}: claim failed {exc}")
            continue
        print(f"\n=== MI{interface} IN {ep_in:#x} OUT {ep_out:#x} ===")
        # drain
        while True:
            try:
                dev.read(ep_in, 4096, timeout=100)
            except Exception:  # noqa: BLE001
                break
        seq = 0x1000
        for addr_first in VARIANTS:
            for sender, receiver in ADDRESS_PAIRS:
                seq += 1
                pkt = build_duml(b"\x00\x00", sender, receiver, seq, addr_first=addr_first)
                try:
                    dev.write(ep_out, pkt, timeout=500)
                except Exception as exc:  # noqa: BLE001
                    print(f"  write failed {sender:#x}->{receiver:#x} addr_first={addr_first}: {exc}")
                    continue
                print(f"  TX {sender:#x}->{receiver:#x} addr_first={addr_first}: {pkt.hex(' ')}")
                end = time.monotonic() + 0.7
                while time.monotonic() < end:
                    try:
                        data = dev.read(ep_in, 4096, timeout=200)
                    except Exception:  # noqa: BLE001
                        continue
                    if not data:
                        continue
                    raw = bytes(data)
                    print(f"    RX {len(raw)} {raw[:120].hex(' ')}")
                    try:
                        parsed = parse_duml(raw)
                        if parsed["cmd_id"] != 0x00 or parsed["cmd_set"] != 0x40:
                            print(f"    parsed {parsed}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    parse {exc}")
        usb.util.release_interface(dev, interface)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
