#!/usr/bin/env python3
"""Verify the logic-link reassembler and documented start packets."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from mobile_receiver import (  # noqa: E402
    LOGICLINK_MAGIC,
    START_PACKETS,
    VIDEO_PORT,
    LogicLinkDecoder,
)


def encap(port: int, payload: bytes) -> bytes:
    return LOGICLINK_MAGIC + port.to_bytes(2, "little") + len(payload).to_bytes(2, "little") + b"\x00\x00" + payload


def main() -> int:
    assert len(START_PACKETS) == 2
    assert START_PACKETS[0].startswith(b"\x55\xcc\x49\x57")
    assert START_PACKETS[1].startswith(b"\x55\xcc\x49\x57")
    decoder = LogicLinkDecoder()
    video = bytes(range(256)) * 4
    stream = encap(VIDEO_PORT, video) + encap(0x3000, b"telemetry")
    # Split in awkward places, including inside the magic.
    out = []
    for i in range(0, len(stream), 7):
        out.extend(decoder.put(stream[i : i + 7]))
    assert [p.port for p in out] == [VIDEO_PORT, 0x3000], [p.port for p in out]
    assert out[0].payload == video
    assert decoder.ports[VIDEO_PORT] == 1
    # Corrupted leading bytes must resynchronise.
    decoder2 = LogicLinkDecoder()
    packets = decoder2.put(b"\x00\x01garbage" + encap(VIDEO_PORT, b"abc"))
    assert len(packets) == 1 and packets[0].payload == b"abc"
    print("logic-link parser OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
