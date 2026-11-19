#!/usr/bin/env python3
"""Generate a synthetic H.264 clip and verify the live decoder."""
from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from decoder import H264Decoder  # noqa: E402


def make_clip(path: Path, width=640, height=360, fps=30, seconds=2):
    container = av.open(str(path), "w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "28"}
    for i in range(fps * seconds):
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[:, :, 0] = (i * 7) % 255
        img[:, :, 1] = (i * 3) % 255
        img[:, :, 2] = 80
        img[height // 4 : height // 2, :, :] = 255
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def main() -> int:
    path = Path(r"D:\g3\captures\synthetic.h264")
    path.parent.mkdir(parents=True, exist_ok=True)
    make_clip(path)
    print("clip", path.stat().st_size, "bytes")
    decoder = H264Decoder()
    total = 0
    with path.open("rb") as fh:
        while chunk := fh.read(4096):
            decoder.feed(chunk)
            frames = decoder.frames()
            total += len(frames)
    print("frames", total, "stats", decoder.stats)
    return 0 if total > 30 and decoder.stats.width == 640 else 1


if __name__ == "__main__":
    raise SystemExit(main())
