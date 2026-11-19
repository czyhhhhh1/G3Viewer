#!/usr/bin/env python3
"""Serve a looping synthetic H.264 stream over RTSP for GStreamer tests."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import av

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from rtsp_server import RTSPServer  # noqa: E402

CLIP = Path(r"D:\g3\captures\synthetic.h264")
PORT = int(os.environ.get("RTSP_PORT", "8555"))


def feed(server: RTSPServer) -> None:
    while True:
        container = av.open(str(CLIP))
        stream = container.streams.video[0]
        for packet in container.demux(stream):
            data = bytes(packet)
            if data:
                server.push(data)
                time.sleep(1 / 30)
        container.close()
        time.sleep(0.5)


def main() -> int:
    server = RTSPServer(port=PORT, log=print)
    server.start()
    import threading

    threading.Thread(target=feed, args=(server,), daemon=True).start()
    print("serving", server.url, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
