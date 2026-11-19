#!/usr/bin/env python3
"""Benchmark low-latency H.264 decode configurations on a 1080p clip."""
from __future__ import annotations

import time
from pathlib import Path

import av
import numpy as np

CLIP = Path(r"D:\g3\captures\test1080.h264")


def frames_from_clip() -> list[bytes]:
    data = CLIP.read_bytes()
    parts = data.split(b"\x00\x00\x00\x01\x09\x10")
    return [p + b"\x00\x00\x00\x01\x09\x10" for p in parts if p]


def bench_current(frames: list[bytes]) -> tuple[int, float]:
    codec = av.CodecContext.create("h264", "r")
    codec.thread_type = "AUTO"
    out = 0
    start = time.perf_counter()
    for frame in frames:
        try:
            for packet in codec.parse(frame):
                out += len(codec.decode(packet))
        except Exception:
            pass
    return out, time.perf_counter() - start


def bench_direct(frames: list[bytes], thread_type: str, threads: int, low_delay: bool) -> tuple[int, float]:
    codec = av.CodecContext.create("h264", "r")
    codec.thread_type = thread_type
    codec.thread_count = threads
    if low_delay:
        codec.flags |= 1 << 19  # AV_CODEC_FLAG_LOW_DELAY
    out = 0
    start = time.perf_counter()
    for frame in frames:
        try:
            out += len(codec.decode(av.Packet(frame)))
        except Exception:
            pass
    return out, time.perf_counter() - start


def main() -> int:
    frames = frames_from_clip()
    print("frames", len(frames), "first", len(frames[0]))
    for label, fn in [
        ("current parse/AUTO", lambda: bench_current(frames)),
        ("direct NONE low", lambda: bench_direct(frames, "NONE", 1, True)),
        ("direct SLICE 2 low", lambda: bench_direct(frames, "SLICE", 2, True)),
        ("direct AUTO low", lambda: bench_direct(frames, "AUTO", 0, True)),
    ]:
        out, elapsed = fn()
        print(f"{label:22s} frames={out:3d} time={elapsed:.3f}s fps={out / elapsed:5.1f} ms/frame={elapsed / max(out,1)*1000:5.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
