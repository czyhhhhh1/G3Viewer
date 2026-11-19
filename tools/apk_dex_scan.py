#!/usr/bin/env python3
"""Search DEX and selected APK entries for USB/RNDIS/video indicators."""
from __future__ import annotations

import argparse
import re
import zipfile

KEYWORDS = [
    b"rndis",
    b"RNDIS",
    b"usb0",
    b"192.168.42",
    b"192.168.2",
    b"logiclink",
    b"Goggles",
    b"goggles",
    b"ZV902",
    b"RMVT",
    b"UsbDevice",
    b"USB_ACCESSORY",
    b"accessory",
    b"Accessory",
    b"usbManager",
    b"USBManager",
    b"22345",
    b"22346",
    b"30000",
    b"videoStream",
    b"VideoStream",
]


def ctx(data: bytes, pos: int, width: int = 100) -> str:
    start = max(0, pos - width)
    end = min(len(data), pos + width)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data[start:end])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("--entry", default=None, help="regex over entry names")
    args = ap.parse_args()
    entry_re = re.compile(args.entry, re.I) if args.entry else None
    with zipfile.ZipFile(args.apk) as z:
        names = z.namelist()
        if entry_re:
            names = [n for n in names if entry_re.search(n)]
        print(f"scanning {len(names)} entries")
        for name in names:
            if not (name.endswith(".dex") or name.endswith(".xml") or name.endswith(".json") or name.endswith(".so")):
                continue
            try:
                data = z.read(name)
            except Exception:  # noqa: BLE001
                continue
            lower = data.lower()
            hits = []
            for kw in KEYWORDS:
                start = 0
                count = 0
                while True:
                    pos = lower.find(kw.lower(), start)
                    if pos < 0:
                        break
                    hits.append((kw.decode(), pos))
                    start = pos + len(kw)
                    count += 1
                    if count >= 3:
                        break
            if hits:
                print(f"\n### {name} ({len(data)} bytes)")
                for kw, pos in hits[:30]:
                    print(f"  {kw:14s} @{pos:#x}: {ctx(data, pos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
