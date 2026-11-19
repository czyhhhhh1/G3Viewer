#!/usr/bin/env python3
"""Scan APK native libraries for USB/RNDIS/video protocol indicators."""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

KEYWORDS = [
    b"rndis",
    b"RNDIS",
    b"logiclink",
    b"ZV902",
    b"Goggles3",
    b"goggles",
    b"MFi",
    b"mfi",
    b"usbmux",
    b"192.168.42",
    b"192.168.2",
    b"22345",
    b"22346",
    b"30000",
    b"usb0",
    b"usb1",
    b"video_port",
    b"video stream",
    b"VideoStream",
    b"RMVT",
]


def printable_context(data: bytes, pos: int, length: int = 80) -> str:
    start = max(0, pos - length)
    end = min(len(data), pos + length)
    chunk = data[start:end]
    return "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("--pattern", default=None, help="extra regex over ASCII strings")
    args = ap.parse_args()
    path = Path(args.apk)
    extra = re.compile(args.pattern.encode(), re.I) if args.pattern else None
    with zipfile.ZipFile(path) as z:
        libs = [n for n in z.namelist() if n.startswith("lib/arm64-v8a/") and n.endswith(".so")]
        print(f"scanning {len(libs)} libs")
        for name in libs:
            data = z.read(name)
            hits = []
            lower = data.lower()
            for kw in KEYWORDS:
                start = 0
                count = 0
                while True:
                    pos = lower.find(kw.lower(), start)
                    if pos < 0:
                        break
                    hits.append((kw.decode(errors="replace"), pos, printable_context(data, pos)))
                    count += 1
                    start = pos + len(kw)
                    if count >= 4:
                        break
            if extra:
                for match in extra.finditer(data):
                    text = match.group().decode("ascii", "replace")
                    hits.append(("regex", match.start(), text))
            if hits:
                print(f"\n### {name} ({len(data)} bytes)")
                for kw, pos, ctx in hits[:40]:
                    print(f"  {kw:12s} @{pos:#x}: {ctx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
