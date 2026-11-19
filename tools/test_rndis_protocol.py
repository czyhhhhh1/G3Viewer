#!/usr/bin/env python3
"""Verify the Python LiveView protocol against the C# reference vectors."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from rndis_receiver import LiveProtocol, create_handshake  # noqa: E402

SESSION = 0xDD3A


def packet(part=0, count=1, sequence=0x1000, payload=b"\x00\x00\x00\x01\x65"):
    data = bytearray(20 + len(payload))
    struct.pack_into("<H", data, 0, len(data) | 0x8000)
    struct.pack_into("<H", data, 2, SESSION)
    struct.pack_into("<H", data, 4, sequence)
    data[6] = 2
    data[7] = 0
    for i in range(7):
        data[7] ^= data[i]
    data[17] = count | ((part & 1) << 7)
    data[18] = (part >> 1) & 0xFF
    data[19] = (part >> 9) & 0xFF
    data[20:] = payload
    return bytes(data)


def main() -> int:
    expected = bytes.fromhex(
        "30803ADD00000057D0E964006400C0051400000A0064006400C005140000640014006400C00514000064000101040A02"
    )
    assert create_handshake(SESSION) == expected
    proto = LiveProtocol(SESSION)
    result = proto.accept(packet())
    assert result.video == b"\x00\x00\x00\x01\x65"
    assert result.acknowledgement is not None
    assert len(result.acknowledgement) == 30
    assert result.acknowledgement[6] == 4
    assert struct.unpack_from("<H", result.acknowledgement, 8)[0] == 0x1000
    # Out of order two-part frame.
    proto = LiveProtocol(SESSION)
    last = packet(1, 2, 0x1008, b"\x03\x04")
    assert proto.accept(last).video is None
    assert proto.accept(last).video is None
    result = proto.accept(packet(0, 2, 0x1000, b"\x01\x02"))
    assert result.video == b"\x01\x02\x03\x04"
    assert proto.video_bytes == 4
    # Sequence wrap.
    proto = LiveProtocol(SESSION)
    assert proto.accept(packet(0, 2, 0xFFF8, b"\x01")).video is None
    assert proto.accept(packet(1, 2, 0, b"\x02")).video == b"\x01\x02"
    print("LiveView protocol OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
