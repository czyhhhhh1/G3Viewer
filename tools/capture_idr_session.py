#!/usr/bin/env python3
"""Capture LiveView frames, logging IDR frames for decoder diagnostics."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from rndis_receiver import RndisVideoReceiver, is_idr  # noqa: E402

out = Path(r"D:\g3\captures\idr-session.bin")
log = Path(r"D:\g3\captures\idr-session.log")
frames: list[bytes] = []
log.write_text("", encoding="utf-8")


def status(message: str) -> None:
    line = f"{time.time():.3f} {message}\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line, end="")


receiver = RndisVideoReceiver(lambda data: frames.append(data), status)
receiver.start()
time.sleep(20)
receiver.stop()
with log.open("a", encoding="utf-8") as fh:
    fh.write(f"frames={len(frames)} keys={sum(is_idr(f) for f in frames)}\n")
    for index, frame in enumerate(frames):
        if is_idr(frame):
            fh.write(f"IDR index={index} len={len(frame)} head={frame[:64].hex()}\n")
out.write_bytes(b"".join(frames))
print(f"frames={len(frames)} keys={sum(is_idr(f) for f in frames)} out={out}")
