"""Low-latency H.264 decoding for live DJI video.

The goggles emit an Annex B H.264 elementary stream. PyAV's parser handles NAL
boundaries and SPS/PPS changes; we only have to feed it bytes as they arrive.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable

import av
import numpy as np

try:
    from av.codec.hwaccel import HWAccel
except Exception:  # noqa: BLE001
    HWAccel = None  # type: ignore[assignment,misc]


@dataclass
class DecoderStats:
    frames: int = 0
    errors: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bitrate_kbps: float = 0.0
    last_error: str = ""


class H264Decoder:
    """Thread-safe-ish incremental H.264 decoder.

    Feed bytes from the network/USB thread; call ``frames()`` from the UI thread.
    Decoded frames are RGB24 numpy arrays.
    """

    def __init__(self, use_hardware: bool = False) -> None:
        self.use_hardware = use_hardware
        self.hardware_active = False
        self.hardware_error = ""
        self._lock = threading.Lock()
        self._codec = self._new_codec()
        self._pending = bytearray()
        self._frames: list[np.ndarray] = []
        self.stats = DecoderStats()
        self._bytes = 0
        self._fps_window: list[float] = []

    def _new_codec(self) -> av.CodecContext:
        codec = None
        if self.use_hardware and HWAccel is not None:
            try:
                codec = av.CodecContext.create("h264", "r", hwaccel=HWAccel("d3d11va"))
                self.hardware_active = True
            except Exception as exc:  # noqa: BLE001
                self.hardware_error = f"{type(exc).__name__}: {exc}"
                self.hardware_active = False
        if codec is None:
            codec = av.CodecContext.create("h264", "r")
            self.hardware_active = False
        # Critical for live video: the default AUTO frame threading buffers
        # ~17 frames at 1080p (~0.5 s at 30 fps) before the first output.
        # Single-threaded + low-delay keeps the decoder to ~1 frame of slack.
        codec.thread_type = "NONE"
        codec.thread_count = 1
        codec.flags |= 1 << 19  # AV_CODEC_FLAG_LOW_DELAY
        return codec

    def reset(self) -> None:
        with self._lock:
            self._codec = self._new_codec()
            self._pending.clear()
            self._frames.clear()
            self.stats = DecoderStats()

    def feed(self, data: bytes, timestamp: float | None = None) -> None:
        if not data:
            return
        with self._lock:
            self._bytes += len(data)
            try:
                packets = self._codec.parse(data)
                for packet in packets:
                    for frame in self._codec.decode(packet):
                        self._append(frame, timestamp)
            except Exception as exc:  # noqa: BLE001 - corrupted live data is normal
                self.stats.errors += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                # A damaged NAL can poison the parser; restart on the next keyframe.
                self._codec = self._new_codec()

    def feed_frame(self, data: bytes, timestamp: float | None = None) -> None:
        """Decode one complete Annex B access unit with no parser buffering.

        LiveView frames arrive as complete access units. Passing them directly
        to libavcodec removes the parser's one-frame delay; fall back to the
        incremental parser if the data is not an access unit.
        """
        if not data:
            return
        with self._lock:
            self._bytes += len(data)
            try:
                for frame in self._codec.decode(av.Packet(data)):
                    self._append(frame, timestamp)
                return
            except Exception:
                pass
            try:
                for packet in self._codec.parse(data):
                    for frame in self._codec.decode(packet):
                        self._append(frame, timestamp)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"

    def _append(self, frame: av.VideoFrame, timestamp: float | None) -> None:
        img = frame.to_ndarray(format="rgb24")
        if img is None or img.size == 0:
            return
        self.stats.frames += 1
        self.stats.width = img.shape[1]
        self.stats.height = img.shape[0]
        self._frames.append(img)
        if len(self._frames) > 8:
            del self._frames[:-8]

    def frames(self) -> list[np.ndarray]:
        with self._lock:
            frames = self._frames
            self._frames = []
            return frames

    def flush(self) -> list[np.ndarray]:
        """Flush delayed frames (file playback / end of stream)."""
        with self._lock:
            try:
                for frame in self._codec.decode(None):
                    self._append(frame, None)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
            frames = self._frames
            self._frames = []
            return frames

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return self._frames[-1] if self._frames else None


class H264StreamDecoder:
    """Convenience wrapper that coalesces decoder output for a GUI."""

    def __init__(self) -> None:
        self.decoder = H264Decoder()

    def feed(self, data: bytes) -> list[np.ndarray]:
        self.decoder.feed(data)
        return self.decoder.frames()

    @property
    def stats(self) -> DecoderStats:
        return self.decoder.stats


def decode_file(path: str) -> Iterable[np.ndarray]:
    decoder = H264Decoder()
    with open(path, "rb") as fh:
        while chunk := fh.read(256 * 1024):
            decoder.feed(chunk)
            yield from decoder.frames()
