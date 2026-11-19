#!/usr/bin/env python3
"""Measure decoder buffering when H.264 access units arrive one at a time."""
from __future__ import annotations

import time

import av

CLIP = r"D:\g3\captures\test1080aud.h264"


def packets():
    container = av.open(CLIP)
    stream = container.streams.video[0]
    for packet in container.demux(stream):
        data = bytes(packet)
        if data:
            yield data
    container.close()


def run(label: str, thread_type: str, threads: int, low: bool) -> None:
    codec = av.CodecContext.create("h264", "r")
    codec.thread_type = thread_type
    codec.thread_count = threads
    if low:
        codec.flags |= 1 << 19
    fed = out = 0
    first_at = None
    max_backlog = 0
    start = time.perf_counter()
    for data in packets():
        fed += 1
        try:
            for packet in codec.parse(data):
                out += len(codec.decode(packet))
        except Exception:
            pass
        if out and first_at is None:
            first_at = fed
        max_backlog = max(max_backlog, fed - out)
    print(
        f"{label:22s} fed={fed:3d} out={out:3d} first_at={first_at} "
        f"max_backlog={max_backlog:2d} time={time.perf_counter()-start:.3f}s"
    )


if __name__ == "__main__":
    for args in [
        ("AUTO 0", "AUTO", 0, False),
        ("AUTO 1 low", "AUTO", 1, True),
        ("SLICE 2 low", "SLICE", 2, True),
        ("NONE 1 low", "NONE", 1, True),
    ]:
        run(*args)
