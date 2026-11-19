#!/usr/bin/env python3
"""Send DJI Goggles start-video DUML commands over the PC-mode vendor link.

This adapts the two documented "logiclink" start commands from the Goggles N3
mobile protocol to the Goggles 3 PC-mode DUML channel (MI04). It rebuilds the
DUML headers with host address 0x03 and goggles address 0xb3, then watches all
five vendor interfaces for a response or video stream.
"""
from __future__ import annotations

import argparse
import pathlib
import threading
import time

import usb.backend.libusb0 as libusb0
import usb.core
import usb.util

from duml import build_duml

VID, PID = 0x2CA3, 0x0020
HOST_ADDR = 0x03
GOGGLE_ADDR = 0xB3
VENDOR_IFACES = (3, 4, 5, 6, 7)

# DUML payloads from the two Goggles N3 start packets (without header / CRC).
PAYLOAD_99 = bytes.fromhex(
    "40 00 99 02 02 00 00 d5 07 00 00 00 00 00 13 00 0d 00 "
    "63 61 6d 63 61 70 5f 63 6f 6d 6d 6f 6e 00 00 00 00"
)
PAYLOAD_88 = bytes.fromhex("40 00 88 17 00 00 23 00 41 50 50 00 00 00 00 00 02")


def get_backend():
    return libusb0.get_backend()


def get_interfaces():
    found = {}
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=get_backend()):
        try:
            cfg = dev.get_active_configuration()
            found[cfg[(0, 0)].bInterfaceNumber] = dev
        except Exception:  # noqa: BLE001
            continue
    return found


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hx:<{width * 3}} {asc}")
    return "\n".join(lines)


class Capture:
    def __init__(self, outdir: pathlib.Path):
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.records: list[tuple[float, int, bytes]] = []
        self.stats = {i: [0, 0] for i in VENDOR_IFACES}
        self.threads = []
        self.files = {}

    def start(self, devices):
        for interface, dev in sorted(devices.items()):
            if interface not in VENDOR_IFACES:
                continue
            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]
            ep_in = next(
                ep.bEndpointAddress
                for ep in intf
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
            )
            fh = (self.outdir / f"mi{interface:02d}.bin").open("wb")
            self.files[interface] = fh
            t = threading.Thread(target=self._read_loop, args=(dev, interface, ep_in, fh), daemon=True)
            t.start()
            self.threads.append(t)
            time.sleep(0.05)

    def _read_loop(self, dev, interface, ep_in, fh):
        while not self.stop.is_set():
            try:
                data = dev.read(ep_in, 256 * 1024, timeout=250)
            except Exception as exc:  # noqa: BLE001
                if "timeout" in str(exc).lower():
                    continue
                print(f"[MI{interface:02d}] read stopped: {exc}")
                return
            if not data:
                continue
            raw = bytes(data)
            with self.lock:
                self.records.append((time.monotonic(), interface, raw))
                self.stats[interface][0] += 1
                self.stats[interface][1] += len(raw)
                fh.write(raw)
                fh.flush()

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=1)
        for fh in self.files.values():
            fh.close()


def open_out(dev, interface):
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep = next(
        ep.bEndpointAddress
        for ep in intf
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    usb.util.claim_interface(dev, interface)
    return ep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=14.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--out", default="captures/start_video")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    devices = get_interfaces()
    if not devices:
        print("DJI gear not found")
        return 1
    if args.interface not in devices:
        print(f"MI{args.interface} not found; have {sorted(devices)}")
        return 1

    packets = [
        build_duml(PAYLOAD_99, HOST_ADDR, GOGGLE_ADDR, 0x0001),
        build_duml(PAYLOAD_88, HOST_ADDR, GOGGLE_ADDR, 0x0002),
    ]
    for idx, pkt in enumerate(packets, 1):
        print(f"packet {idx} ({len(pkt)} bytes): {pkt.hex(' ')}")
    if args.dry_run:
        return 0

    outdir = pathlib.Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    cap = Capture(outdir)
    cap.start(devices)

    ep_out = open_out(devices[args.interface], args.interface)
    print(f"writing to MI{args.interface} EP {ep_out:#04x}")
    try:
        start = time.monotonic()
        next_send = start
        while time.monotonic() - start < args.seconds:
            if time.monotonic() >= next_send:
                for idx, pkt in enumerate(packets, 1):
                    written = devices[args.interface].write(ep_out, pkt, timeout=2000)
                    print(f"[{time.monotonic() - start:5.2f}s] TX packet {idx}: {written} bytes")
                    time.sleep(0.05)
                next_send += args.interval
            time.sleep(0.05)
    finally:
        cap.close()
        try:
            usb.util.release_interface(devices[args.interface], args.interface)
        except Exception:  # noqa: BLE001
            pass

    print("\nSummary:")
    for interface in VENDOR_IFACES:
        packets_n, bytes_n = cap.stats[interface]
        print(f"  MI{interface:02d}: {packets_n} packets, {bytes_n} bytes")
    if cap.records:
        print("\nFirst packets:")
        with cap.lock:
            for ts, interface, raw in cap.records[:20]:
                print(f"[MI{interface:02d}] {len(raw)} bytes @{ts:.3f}\n{hexdump(raw[:128])}")
    print(f"saved to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
