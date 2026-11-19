#!/usr/bin/env python3
"""Probe the goggles RNDIS network without changing host routes.

Windows exposes IP_UNICAST_IF (option 31) which pins a socket to an interface.
This lets us try candidate DJI subnets even though the adapter only has an
APIPA address.
"""
from __future__ import annotations

import argparse
import socket
import struct
import time

IF_INDEX = 6
IP_UNICAST_IF = 31
SO_BINDTODEVICE = 25  # not supported on Windows, kept for reference

CANDIDATES = [
    "192.168.42.1",
    "192.168.42.2",
    "192.168.2.1",
    "192.168.1.1",
    "192.168.0.1",
    "192.168.8.1",
    "192.168.100.1",
    "10.0.0.1",
    "10.42.0.1",
    "172.20.10.1",
    "169.254.42.1",
    "169.254.100.1",
]
PORTS = [80, 443, 554, 8554, 8080, 8888, 5000, 22345, 22346, 30000, 22, 23, 21]


def try_bind(sock):
    for value in (IF_INDEX, struct.unpack("<I", struct.pack(">I", IF_INDEX))[0]):
        try:
            sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, value)
            return value
        except OSError as exc:
            last = exc
    return None


def probe(ip, port, timeout=0.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        bound = try_bind(s)
        if bound is None:
            return "bind-failed"
        s.connect((ip, port))
        return "OPEN"
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default=",".join(map(str, PORTS)))
    ap.add_argument("--timeout", type=float, default=0.6)
    args = ap.parse_args()
    ports = [int(p) for p in args.ports.split(",") if p]
    test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print("IP_UNICAST_IF value:", try_bind(test))
    test.close()
    found = []
    for ip in CANDIDATES:
        for port in ports:
            start = time.monotonic()
            result = probe(ip, port, args.timeout)
            elapsed = (time.monotonic() - start) * 1000
            if result not in ("TimeoutError", "ConnectionRefusedError", "OSError"):
                print(f"{ip}:{port:<5} {result:24s} {elapsed:6.1f}ms")
                if result == "OPEN":
                    found.append((ip, port))
    print("open:", found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
