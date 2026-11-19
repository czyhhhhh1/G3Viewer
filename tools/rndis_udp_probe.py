#!/usr/bin/env python3
"""Try the documented DJI RNDIS UDP handshake against the goggles.

Pins the UDP socket to the RNDIS interface with IP_UNICAST_IF so no host route
or static IP change is required. Listens for any response on the same port.
"""
from __future__ import annotations

import argparse
import socket
import struct
import time

IF_INDEX = 6
IP_UNICAST_IF = 31
LOCAL_PORT = 12346

HANDSHAKE = bytes([
    0x30, 0x80, 0x3A, 0xDD, 0x00, 0x00, 0x00, 0x57, 0xD0, 0xE9, 0x64, 0x00, 0x64, 0x00, 0xC0, 0x05,
    0x14, 0x00, 0x00, 0x0A, 0x00, 0x64, 0x00, 0x64, 0x00, 0xC0, 0x05, 0x14, 0x00, 0x00, 0x64, 0x00,
    0x14, 0x00, 0x64, 0x00, 0xC0, 0x05, 0x14, 0x00, 0x00, 0x64, 0x00, 0x01, 0x01, 0x04, 0x0A, 0x02,
])

TARGETS = [
    "192.168.42.2",
    "192.168.42.1",
    "192.168.2.1",
    "192.168.2.20",
    "192.168.42.20",
    "169.254.42.2",
]


def pin_if(sock):
    # Windows IP_UNICAST_IF wants the interface index in network byte order.
    value = struct.unpack("<I", struct.pack(">I", IF_INDEX))[0]
    sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, value)
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--port", type=int, default=9003)
    args = ap.parse_args()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        rx.bind(("0.0.0.0", LOCAL_PORT))
    except OSError as exc:
        print(f"bind {LOCAL_PORT} failed: {exc}")
        return 1
    rx.settimeout(0.3)
    print(f"listening on 0.0.0.0:{LOCAL_PORT}")

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print("IP_UNICAST_IF", hex(pin_if(tx)))
    tx.bind(("0.0.0.0", LOCAL_PORT))

    for target in TARGETS:
        try:
            tx.sendto(HANDSHAKE, (target, args.port))
            print(f"TX handshake -> {target}:{args.port}")
        except Exception as exc:  # noqa: BLE001
            print(f"TX {target} failed: {exc}")

    end = time.monotonic() + args.seconds
    seen = []
    while time.monotonic() < end:
        try:
            data, addr = rx.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception as exc:  # noqa: BLE001
            print("RX error", exc)
            break
        seen.append((addr, data))
        print(f"RX {len(data)} from {addr}: {data[:80].hex(' ')}")
    if not seen:
        print("no response")
    rx.close()
    tx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
