#!/usr/bin/env python3
"""Stream through every APK entry looking for DJI RNDIS/UDP constants."""
from __future__ import annotations

import zipfile

NEEDLES = [
    b"192.168.42",
    b"192.168.2.1",
    b"9003",
    b"12346",
    b"rndis",
    b"RNDIS",
    b"usb0",
    b"Goggles3",
    b"ZV902",
    b"logiclink",
    b"22345",
    b"22346",
]


def ctx(data: bytes, pos: int, width: int = 80) -> str:
    start = max(0, pos - width)
    end = min(len(data), pos + width)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data[start:end])


def main() -> int:
    apk = r"D:\g3\vendor\djifly-1.21.16.apk"
    with zipfile.ZipFile(apk) as z:
        for info in z.infolist():
            if info.file_size > 80 * 1024 * 1024:
                continue
            try:
                data = z.read(info)
            except Exception:  # noqa: BLE001
                continue
            for needle in NEEDLES:
                pos = data.lower().find(needle.lower())
                if pos >= 0:
                    print(f"{info.filename} [{needle.decode(errors='replace')}] @{pos:#x}: {ctx(data, pos)}")
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
