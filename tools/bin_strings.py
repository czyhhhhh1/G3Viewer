#!/usr/bin/env python3
"""Extract printable ASCII/UTF-16 strings and PE exports/imports from a binary."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def ascii_strings(data: bytes, minimum: int = 5):
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % minimum)
    for match in pattern.finditer(data):
        yield match.start(), match.group().decode("ascii", "replace")


def utf16_strings(data: bytes, minimum: int = 5):
    pattern = re.compile((r"(?:[\x20-\x7e]\x00){%d,}" % minimum).encode())
    for match in pattern.finditer(data):
        yield match.start(), match.group().decode("utf-16le", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--min", type=int, default=5)
    ap.add_argument("--grep", default=None)
    ap.add_argument("--utf16", action="store_true")
    ap.add_argument("--limit", type=int, default=20000)
    args = ap.parse_args()
    path = Path(args.file)
    data = path.read_bytes()
    print(f"# {path} {len(data)} bytes")
    try:
        import pefile

        pe = pefile.PE(data=data, fast_load=True)
        print(f"# machine={pe.FILE_HEADER.Machine:#x} timestamp={pe.FILE_HEADER.TimeDateStamp}")
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
            print("# exports:")
            for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                print(f"  {exp.ordinal:4d} {exp.name.decode() if exp.name else '?'} {exp.address:#x}")
        print("# imports:")
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                names = [imp.name.decode() if imp.name else f"ord{imp.ordinal}" for imp in entry.imports]
                print(f"  {entry.dll.decode()}: {', '.join(names[:20])}")
    except Exception as exc:  # noqa: BLE001
        print(f"# PE parse failed: {exc}")

    matcher = re.compile(args.grep, re.I) if args.grep else None
    count = 0
    for offset, text in ascii_strings(data, args.min):
        if matcher and not matcher.search(text):
            continue
        print(f"{offset:08x}  {text}")
        count += 1
        if count >= args.limit:
            break
    if args.utf16:
        for offset, text in utf16_strings(data, args.min):
            if matcher and not matcher.search(text):
                continue
            print(f"{offset:08x}  [w] {text}")
            count += 1
            if count >= args.limit:
                break
    print(f"# {count} strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
