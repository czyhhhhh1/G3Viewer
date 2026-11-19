#!/usr/bin/env python3
"""Send a raw Annex B H.264 file to G3Viewer over UDP, for local testing."""
from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5004)
    ap.add_argument("--chunk", type=int, default=1400)
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    data = Path(args.file).read_bytes()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    start = time.monotonic()
    while True:
        for i in range(0, len(data), args.chunk):
            sock.sendto(data[i : i + args.chunk], (args.host, args.port))
            sent += 1
            time.sleep(0.001)
        if not args.loop:
            break
        time.sleep(0.2)
        if time.monotonic() - start > 30:
            break
    print(f"sent {sent} datagrams, {len(data)} bytes per loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
