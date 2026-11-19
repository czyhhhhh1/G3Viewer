#!/usr/bin/env python3
"""Test DJI's RNDIS/UDP video path against the goggles.

Requires the host RNDIS interface to have an address in the goggle's subnet,
e.g. 192.168.42.1/24. The companion `rndis_uac_test.ps1` adds that address
temporarily and removes it afterwards.

This is intentionally a standalone research tool: the main GUI only consumes
raw H.264 over UDP/USB, so it does not need administrator rights.
"""
from __future__ import annotations

import argparse
import pathlib
import socket
import struct
import time

HANDSHAKE = bytes([
    0x30, 0x80, 0x3A, 0xDD, 0x00, 0x00, 0x00, 0x57, 0xD0, 0xE9, 0x64, 0x00, 0x64, 0x00, 0xC0, 0x05,
    0x14, 0x00, 0x00, 0x0A, 0x00, 0x64, 0x00, 0x64, 0x00, 0xC0, 0x05, 0x14, 0x00, 0x00, 0x64, 0x00,
    0x14, 0x00, 0x64, 0x00, 0xC0, 0x05, 0x14, 0x00, 0x00, 0x64, 0x00, 0x01, 0x01, 0x04, 0x0A, 0x02,
])

KEEPALIVE = bytes([
    0x1e, 0x80, 0x3a, 0xdd, 0x00, 0x00, 0x04, 0x7d, 0x08, 0xea, 0x08, 0xea, 0x00, 0x00, 0xd0, 0xe9,
    0xd0, 0xe9, 0x00, 0x00, 0xd0, 0xe9, 0xd8, 0xe9, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])

VIDEO_MAGIC = b"\x00\x00\x00\x01"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="192.168.42.2")
    ap.add_argument("--port", type=int, default=9003)
    ap.add_argument("--local-port", type=int, default=12346)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--out", default="captures/rndis-test.h264")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.local_port))
    sock.settimeout(0.5)

    output = pathlib.Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"")
    total_video = 0
    video_packets = 0
    types: dict[int, int] = {}
    session = None
    end = time.monotonic() + args.seconds
    last_handshake = 0.0

    print(f"target {args.target}:{args.port}, local {args.local_port}, {args.seconds}s")
    while time.monotonic() < end:
        now = time.monotonic()
        if now - last_handshake > 4.0:
            sock.sendto(HANDSHAKE, (args.target, args.port))
            last_handshake = now
            print("TX handshake")
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if len(data) < 8:
            continue
        expected = struct.unpack("<H", data[0:2])[0] & 0x7FFF
        if expected != len(data):
            print(f"RX {len(data)} bad length header {expected}")
            continue
        ptype = data[6]
        types[ptype] = types.get(ptype, 0) + 1
        if session is None:
            session = data[2] | (data[3] << 8)
            print(f"session {session:#06x}, first packet type {ptype} from {addr}")
        seq = data[4] | (data[5] << 8)
        if ptype == 0:
            print(f"  handshake response {data.hex(' ')}")
        elif ptype == 1:
            print(f"  telemetry len={len(data)} seq={seq}")
        elif ptype == 2:
            frame = data[0x10]
            n_parts = data[0x11] & 0x7F
            part = (data[0x11] >> 7) + ((data[0x12] & 0x1F) << 1)
            payload = data[20:]
            with output.open("ab") as fh:
                fh.write(payload)
            total_video += len(payload)
            video_packets += 1
            if video_packets <= 5 or part == n_parts - 1:
                print(f"  video frame={frame} part={part}/{n_parts} seq={seq} bytes={len(payload)}")
            if part == n_parts - 1:
                ack = bytearray(KEEPALIVE)
                ack[8:10] = struct.pack("<H", seq)
                ack[10:12] = struct.pack("<H", seq)
                sock.sendto(bytes(ack), (args.target, args.port))
        else:
            print(f"  type {ptype} len={len(data)} seq={seq}")
    sock.close()
    print(f"types {types}")
    print(f"video packets={video_packets}, bytes={total_video}, file={output}")
    if total_video == 0:
        print("no video. Check: goggle is connected to an aircraft/air unit, the host IP is 192.168.42.1/24,")
        print("and the phone/MFI path is not the only video path on this firmware.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
