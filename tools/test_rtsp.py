#!/usr/bin/env python3
"""Feed a synthetic H.264 clip into the RTSP server and read it with FFmpeg."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import av
import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from rtsp_server import RTSPServer, split_annexb  # noqa: E402

CLIP = Path(r"D:\g3\captures\synthetic.h264")
PORT = 8555


def feed(server: RTSPServer) -> None:
    container = av.open(str(CLIP))
    stream = container.streams.video[0]
    for packet in container.demux(stream):
        data = bytes(packet)
        if not data:
            continue
        server.push(data)
        time.sleep(1 / 30)
    time.sleep(2)


def main() -> int:
    nals = split_annexb(CLIP.read_bytes()[:100000])
    assert nals and nals[0][0] & 0x1F in (7, 9), [n[0] for n in nals[:3]]
    server = RTSPServer(port=PORT, log=print)
    server.start()
    threading.Thread(target=feed, args=(server,), daemon=True).start()
    time.sleep(1)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "udp",
        "-i",
        f"rtsp://127.0.0.1:{PORT}/fpv_stream",
        "-frames:v",
        "12",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    server.stop()
    print("ffmpeg exit", result.returncode)
    print(result.stderr[-2000:])
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
