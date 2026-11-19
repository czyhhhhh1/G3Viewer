#!/usr/bin/env python3
"""G3Viewer - DJI Goggles 3 USB / network video receiver for Windows.

The app has two independent parts:
  * PC/OTG diagnostics (read-only, works with the normal 2ca3:0020 mode)
  * Mobile/MFI video receiver (2ca3:1002, after a USB role-switch bridge)
plus a raw H.264 UDP receiver for a Raspberry Pi/Cosmostreamer bridge.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

ROOT = (
    pathlib.Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else pathlib.Path(__file__).resolve().parents[1]
)
APP_DIR = ROOT / "app"
if APP_DIR.is_dir():
    sys.path.insert(0, str(APP_DIR))

from decoder import H264Decoder  # noqa: E402
from mobile_receiver import MobileVideoReceiver, ReceiverStats, VIDEO_PORT  # noqa: E402
from pc_diag import describe_pc_mode  # noqa: E402
from rndis_receiver import (  # noqa: E402
    RndisStats,
    RndisVideoReceiver,
    check_rndis_ip,
    prepare_rndis_ip,
    probe_rndis,
)
from rtsp_server import RTSPServer  # noqa: E402

APP_TITLE = "G3Viewer · DJI Goggles 3 收流器"
BG = "#0d1117"
PANEL = "#161b22"
PANEL_2 = "#21262d"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#2f81f7"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"


class QueueWriter:
    """Small helper for thread-safe log delivery to Tk."""

    def __init__(self, q: queue.Queue[str]) -> None:
        self.q = q

    def __call__(self, message: str) -> None:
        self.q.put(message)


class G3Viewer(tk.Tk):
    def __init__(self, cli_args: argparse.Namespace | None = None) -> None:
        super().__init__()
        self.cli_args = cli_args
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.diag_queue: queue.Queue[str] = queue.Queue()
        self.state_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.log_writer = QueueWriter(self.log_queue)

        self.decoder = H264Decoder()
        self.receiver: MobileVideoReceiver | None = None
        self.rndis: RndisVideoReceiver | None = None
        self.decode_thread: threading.Thread | None = None
        self.decode_stop = threading.Event()
        self.decode_queue: queue.Queue[bytes] = queue.Queue(maxsize=30)
        self.decode_drops = 0
        self.rtsp: RTSPServer | None = None
        self.udp_sock: socket.socket | None = None
        self.udp_thread: threading.Thread | None = None
        self.udp_stop = threading.Event()
        self.file_thread: threading.Thread | None = None
        self.file_stop = threading.Event()
        self.raw_file = None
        self.raw_path: pathlib.Path | None = None
        self.rndis_prepare_stop = threading.Event()
        self.last_frame: np.ndarray | None = None
        self.photo = None
        self.session_started = 0.0
        self.last_stats = time.monotonic()
        self.last_frames = 0
        self.last_video_bytes = 0
        self.fps = 0.0
        self.bitrate_kbps = 0.0
        self.displayed = 0
        self.total_video_bytes = 0
        self.decode_ms = 0.0
        self.render_ms = 0.0
        self._last_error_count = 0
        self.source_kind = ""
        self._build_style()
        self._build_ui()
        self.after(15, self._pump)
        self.after(500, self._stats_tick)
        self.log("G3Viewer 已启动。先看“协议说明”，再选择收流源。")
        try:
            self.rtsp = RTSPServer(log=self.log_writer)
            self.rtsp.start()
        except OSError as exc:
            self.rtsp = None
            self.log(f"Mission Planner RTSP 启动失败（8554 端口）：{exc}", "error")
        if self.cli_args is not None:
            self.after(600, self._autostart)

    def _autostart(self) -> None:
        args = self.cli_args
        if args is None:
            return
        if args.hardware:
            self.decoder_var.set("硬件 (D3D11VA)")
        if args.rndis:
            self.source_var.set("USB RNDIS · LiveView 共享")
            self.start_source()
        elif args.udp:
            self.source_var.set("UDP 裸 H.264")
            self.port_var.set(str(args.udp))
            self.bind_var.set(args.bind)
            self.start_source()
        elif args.file:
            self._start_file_path(args.file)

    # ------------------------------------------------------------------ UI
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL_2, bordercolor=PANEL_2)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", background=PANEL_2, foreground=FG, padding=(12, 7), borderwidth=0)
        style.map("TButton", background=[("active", "#30363d"), ("disabled", "#1b2026")])
        style.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(14, 8), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#1f6feb"), ("disabled", "#1b2026")])
        style.configure("Danger.TButton", background="#5a1e1e", foreground="#ffb3b3", padding=(12, 7))
        style.map("Danger.TButton", background=[("active", "#7a2626")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(18, 9))
        style.map("TNotebook.Tab", background=[("selected", PANEL_2)], foreground=[("selected", FG)])
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=FG, arrowcolor=FG)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL_2), ("active", PANEL_2)],
            foreground=[("readonly", FG)],
            selectbackground=[("readonly", PANEL_2)],
            selectforeground=[("readonly", FG)],
        )
        style.configure("TEntry", fieldbackground=PANEL_2, foreground=FG, insertcolor=FG)
        style.map(
            "TEntry",
            fieldbackground=[("!disabled", PANEL_2)],
            foreground=[("!disabled", FG)],
        )
        style.configure("TScrollbar", background=PANEL_2)
        self.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(18, 12, 18, 4))
        header.pack(fill="x")
        ttk.Label(header, text="G3Viewer", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="DJI Goggles 3 · USB LiveView / 手机模式 / UDP H.264",
            style="Sub.TLabel",
        ).pack(side="left", padx=(14, 0), pady=(5, 0))
        self.status_dot = tk.Canvas(header, width=14, height=14, bg=BG, highlightthickness=0)
        self.status_dot.pack(side="right", padx=(8, 0))
        self._dot = self.status_dot.create_oval(3, 3, 11, 11, fill="#484f58", outline="")
        self.status_text = ttk.Label(header, text="空闲", style="Sub.TLabel")
        self.status_text.pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(8, 14))
        self.video_tab = ttk.Frame(self.notebook, padding=10)
        self.diag_tab = ttk.Frame(self.notebook, padding=10)
        self.guide_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.video_tab, text="  视频监看  ")
        self.notebook.add(self.diag_tab, text="  USB 诊断  ")
        self.notebook.add(self.guide_tab, text="  协议说明  ")
        self._build_video_tab()
        self._build_diag_tab()
        self._build_guide_tab()

    def _build_video_tab(self) -> None:
        self.video_tab.columnconfigure(0, weight=1)
        self.video_tab.rowconfigure(1, weight=1)

        bar = ttk.Frame(self.video_tab, style="Panel.TFrame", padding=10)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar, text="源", style="Panel.TLabel").pack(side="left")
        self.source_var = tk.StringVar(value="USB RNDIS · LiveView 共享")
        self.source_combo = ttk.Combobox(
            bar,
            textvariable=self.source_var,
            values=[
                "USB RNDIS · LiveView 共享",
                "手机模式 USB (2ca3:1002)",
                "UDP 裸 H.264",
                "本地 H.264 文件",
            ],
            state="readonly",
            width=27,
        )
        self.source_combo.pack(side="left", padx=(8, 14))
        self.port_var = tk.StringVar(value="5004")
        ttk.Label(bar, text="UDP 端口", style="Panel.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.port_var, width=7).pack(side="left", padx=(6, 14))
        self.bind_var = tk.StringVar(value="127.0.0.1")
        ttk.Label(bar, text="监听", style="Panel.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.bind_var, width=12).pack(side="left", padx=(6, 10))
        ttk.Label(bar, text="解码", style="Panel.TLabel").pack(side="left")
        self.decoder_var = tk.StringVar(value="软件 (CPU)")
        self.decoder_combo = ttk.Combobox(
            bar,
            textvariable=self.decoder_var,
            values=["软件 (CPU)", "硬件 (D3D11VA)"],
            state="readonly",
            width=15,
        )
        self.decoder_combo.pack(side="left", padx=(6, 12))
        self.connect_btn = ttk.Button(bar, text="开始", style="Accent.TButton", command=self.start_source)
        self.connect_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="停止", style="Danger.TButton", command=self.stop_source, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.record_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="录制原始 H.264", variable=self.record_var).pack(side="left", padx=(18, 0))

        canvas_frame = tk.Frame(self.video_tab, bg="#000000", highlightbackground=PANEL_2, highlightthickness=1)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        self.canvas = tk.Canvas(canvas_frame, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._redraw_last())
        self.canvas.create_text(
            480,
            260,
            text="等待视频流\n\nUSB：眼镜开启 OTG + LiveView sharing 后选第一项\n网络桥：UDP 监听端口 5004",
            fill="#30363d",
            font=("Segoe UI", 16),
            justify="center",
            tags="hint",
        )

        bottom = ttk.Frame(self.video_tab, style="Panel.TFrame", padding=(10, 8))
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.res_label = ttk.Label(bottom, text="-- × --", style="Panel.TLabel")
        self.res_label.pack(side="left", padx=(0, 18))
        self.fps_label = ttk.Label(bottom, text="0.0 fps", style="Panel.TLabel")
        self.fps_label.pack(side="left", padx=(0, 18))
        self.br_label = ttk.Label(bottom, text="0 kbps", style="Panel.TLabel")
        self.br_label.pack(side="left", padx=(0, 18))
        self.pkt_label = ttk.Label(bottom, text="0 包", style="Muted.TLabel")
        self.pkt_label.pack(side="left", padx=(0, 18))
        self.lat_label = ttk.Label(bottom, text="解码 -- ms · 渲染 -- ms", style="Muted.TLabel")
        self.lat_label.pack(side="left")
        self.save_btn = ttk.Button(bottom, text="保存当前帧", command=self.save_frame)
        self.save_btn.pack(side="right")
        self.rec_label = ttk.Label(bottom, text="", style="Muted.TLabel")
        self.rec_label.pack(side="right", padx=(0, 12))

        self.log_text = tk.Text(
            self.video_tab,
            height=6,
            bg=PANEL,
            fg=MUTED,
            insertbackground=FG,
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
            state="disabled",
            wrap="word",
        )
        self.log_text.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=RED)

    def _build_diag_tab(self) -> None:
        self.diag_tab.columnconfigure(0, weight=1)
        self.diag_tab.rowconfigure(1, weight=1)
        bar = ttk.Frame(self.diag_tab, style="Panel.TFrame", padding=10)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(bar, text="扫描 PC/OTG 模式", style="Accent.TButton", command=self.run_diag).pack(side="left")
        ttk.Button(bar, text="检查 LiveView / RNDIS", command=self.check_rndis).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="检查手机模式设备", command=self.check_mobile).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清空", command=self.clear_diag).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="复制 Mission Planner RTSP", command=self.copy_rtsp).pack(side="right", padx=(8, 0))
        ttk.Button(bar, text="打开 Zadig", command=self.open_zadig).pack(side="right")
        self.diag_text = tk.Text(
            self.diag_tab,
            bg="#0b0f14",
            fg="#c9d1d9",
            insertbackground=FG,
            relief="flat",
            font=("Consolas", 10),
            padx=14,
            pady=12,
            wrap="none",
        )
        self.diag_text.grid(row=1, column=0, sticky="nsew")
        self.diag_text.insert(
            "1.0",
            "点“扫描 PC/OTG 模式”读取真实 USB 描述符。\n"
            "这个模式只用于素材导出/升级；扫描结果会明确列出有没有视频接口。\n\n"
            "手机模式需要先用 USB 角色切换桥（如 Cynthion）把眼镜切到 2ca3:1002。\n",
        )
        self.diag_text.configure(state="disabled")

    def _build_guide_tab(self) -> None:
        text = tk.Text(
            self.guide_tab,
            bg="#0b0f14",
            fg="#c9d1d9",
            insertbackground=FG,
            relief="flat",
            font=("Microsoft YaHei UI", 10),
            padx=18,
            pady=16,
            wrap="word",
        )
        text.pack(fill="both", expand=True)
        guide = (
            "DJI Goggles 3：正确的 PC 收流方式\n"
            "================================\n\n"
            "眼镜支持两种完全不同的 USB 输出：\n"
            "  A. PC/OTG 文件模式：2CA3:0020，RNDIS/U 盘/5 个 Bulk，只有状态心跳。\n"
            "  B. LiveView 共享：仍然显示 2CA3:0020，但 RNDIS 网卡会承载视频。\n"
            "     Windows 需要给这个网卡加 192.168.60.1/24，视频来自\n"
            "     192.168.60.2:9003（UDP），不是 Bulk 端点，也不是 UVC。\n\n"
            "你之前“天空端已连、软件没画面”的原因：眼镜只进了 OTG，\n"
            "没有开启 LiveView sharing；MI04 心跳不会因为天空端而变成视频。\n\n"
            "正确操作（不需要 Cynthion、不需要手机）\n"
            "--------------------------------------\n"
            "1. 天空端/飞行器开机，眼镜确认能看到图传；\n"
            "2. 眼镜：设置 > 关于 > 打开“OTG 有线连接模式”；\n"
            "3. 眼镜：按 5D 键向后，打开“LiveView 共享/实时画面共享到手机”\n"
            "   （名字写着 Wi-Fi，但本程序走 USB，电脑不用连眼镜 Wi-Fi）；\n"
            "4. USB-C 数据线接电脑；\n"
            "5. 本程序源选“USB RNDIS · LiveView 共享”，点开始；\n"
            "6. 首次会弹一次 UAC，只给 VID 2CA3:PID 0020 的 RNDIS 网卡加\n"
            "   192.168.60.1/24，不改默认网关/DNS/防火墙；\n"
            "7. 程序发 LiveView 握手，收 H.264 并在窗口里解码显示。\n\n"
            "如果一直没有画面\n"
            "----------------\n"
            "- 确认 LiveView sharing 是开启状态，然后拔插一次 USB 线；\n"
            "- 确认没有别的程序占用 12346 端口或眼镜 USB 设备；\n"
            "- 在“USB 诊断”页点“检查 LiveView / RNDIS”，确认 192.168.60.1\n"
            "  已配置且本地绑定成功；\n"
            "- 若 UAC 被拒绝，可用管理员 PowerShell 手动执行：\n"
            "  New-NetIPAddress -InterfaceIndex <眼镜网卡> `\n"
            "    -IPAddress 192.168.60.1 -PrefixLength 24\n"
            "  删除时用 Remove-NetIPAddress 删除同一个地址。\n\n"
            "Mission Planner 输出\n"
            "--------------------\n"
            "程序启动后会自动输出低延迟 RTSP：\n"
            "  rtsp://127.0.0.1:8554/fpv_stream\n"
            "在“USB 诊断”页点“复制 Mission Planner RTSP”可复制地址。\n"
            "Mission Planner：右键 HUD > Video > Set GStreamer Source，粘贴：\n"
            "  rtspsrc location=rtsp://127.0.0.1:8554/fpv_stream latency=1 "
            "protocols=udp udp-reconnect=1 timeout=0 do-retransmission=false ! "
            "application/x-rtp,media=video,encoding-name=H264 ! rtph264depay ! "
            "h264parse ! queue max-size-buffers=1 leaky=2 ! avdec_h264 ! "
            "queue max-size-buffers=1 leaky=2 ! videoconvert ! "
            "video/x-raw,format=BGRA ! appsink name=outsink sync=false\n"
            "需要 Mission Planner 的 GStreamer 运行时；没有时 MP 会提示下载。\n"
            "本程序输出的是原始 H.264，不重新编码，附加延迟只有 RTP/MP 解码。\n\n"
            "协议要点（来自 MIT 项目 Goggle 3 LiveView）\n"
            "-------------------------------------------\n"
            "握手：48 字节，session 在 offset 2..3，offset 7 = 前 7 字节 XOR；\n"
            "     每 1 秒重发，直到收到第一帧。\n"
            "视频：UDP 包类型 0x02；offset 17..19 是 part/count；\n"
            "     offset 20.. 是 H.264 分片；同一帧按 sequence & 0xfff8 重组；\n"
            "     最后一包回 30 字节 ACK（类型 0x04）。\n"
            "数据：192.168.60.2:9003 -> 192.168.60.1:12346。\n\n"
            "备用路线\n"
            "--------\n"
            "- 手机/MFI 模式：Cynthion 角色切换后走 2CA3:1002 + 0x55CC，\n"
            "  程序里选“手机模式 USB”。\n"
            "- 树莓派/Cosmostreamer 桥：转发裸 H.264 到本程序 UDP 5004。\n"
            "- Wi-Fi 共享：DJI 私有流，只能 DJI Fly 解，不适合 PC 直连。\n"
        )
        text.insert("1.0", guide)
        text.configure(state="disabled")

    # ------------------------------------------------------------- logging
    def log(self, message: str, level: str = "info") -> None:
        self.log_queue.put(f"{time.strftime('%H:%M:%S')}  {message}")

    def _write_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        # Keep the log bounded.
        if int(self.log_text.index("end-1c").split(".")[0]) > 300:
            self.log_text.delete("1.0", "80.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_state(self, state: str, color: str) -> None:
        self.status_text.configure(text=state)
        self.status_dot.itemconfigure(self._dot, fill=color)

    # ------------------------------------------------------------- sources
    def _new_decoder(self) -> None:
        hardware = self.decoder_var.get().startswith("硬件")
        self.decoder = H264Decoder(use_hardware=hardware)
        self.decode_drops = 0
        if hardware:
            if self.decoder.hardware_active:
                self.log("硬件解码：D3D11VA 已启用")
            else:
                self.log(f"硬件解码不可用，已回退软件：{self.decoder.hardware_error or 'unknown'}", "error")

    def _begin_pipeline(self) -> None:
        """Start the decode worker so UDP reception never blocks on decode."""
        self.decode_stop.clear()
        self.decode_queue = queue.Queue(maxsize=30)
        self.decode_thread = threading.Thread(target=self._decode_loop, name="h264-decode", daemon=True)
        self.decode_thread.start()

    def _decode_loop(self) -> None:
        while not self.decode_stop.is_set():
            try:
                data = self.decode_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None:
                break
            started = time.perf_counter()
            try:
                if self.source_kind == "rndis" and not self.decoder.hardware_active:
                    # Software decoder tolerates direct access units and saves
                    # one frame of parser delay.
                    self.decoder.feed_frame(data)
                elif self.source_kind == "rndis":
                    # D3D11VA is stricter about multi-slice access units. Run
                    # the standard H.264 parser for hardware decode; this fixes
                    # motion-time corruption at the cost of one frame (~33 ms).
                    self.decoder.feed(data)
                else:
                    self.decoder.feed(data)
            except Exception as exc:  # noqa: BLE001
                self.log_writer(f"解码线程异常：{exc}")
            self.decode_ms = (time.perf_counter() - started) * 1000

    def _stop_decode_worker(self) -> None:
        self.decode_stop.set()
        try:
            self.decode_queue.put_nowait(None)
        except queue.Full:
            pass
        if self.decode_thread:
            self.decode_thread.join(timeout=1)
            self.decode_thread = None

    def start_source(self) -> None:
        source = self.source_var.get()
        if source.startswith("USB RNDIS"):
            self._start_rndis()
        elif source.startswith("手机模式"):
            self._start_usb()
        elif source.startswith("UDP"):
            self._start_udp()
        else:
            self._start_file()

    def _start_rndis(self) -> None:
        self._stop_all_silent()
        self._new_decoder()
        self._reset_stats()
        self._open_recording()
        self.source_kind = "rndis"
        self._begin_pipeline()
        self.session_started = time.monotonic()
        self.rndis_prepare_stop.clear()
        self._set_state("检查 RNDIS 网卡", AMBER)
        self._set_buttons(True)
        self.log("检查眼镜 RNDIS 网卡与 LiveView 设置...")
        threading.Thread(target=self._prepare_rndis_worker, name="prepare-rndis", daemon=True).start()

    def _prepare_rndis_worker(self) -> None:
        if not check_rndis_ip():
            self.log_writer("首次连接需要一次 UAC：只给 VID 2CA3:PID 0020 的网卡添加 192.168.60.1/24。")
        ok, message = prepare_rndis_ip(self.log_writer)
        if self.rndis_prepare_stop.is_set():
            return
        if not ok:
            self.log_writer(f"RNDIS 配置失败：{message}")
            self.state_queue.put(("RNDIS 配置失败", RED))
            self.state_queue.put(("__enable_connect__", ""))
            return
        self.log_writer(message)
        self.state_queue.put(("等待 LiveView 视频", AMBER))
        self.state_queue.put(("__start_rndis_receiver__", ""))

    def _begin_rndis_receiver(self) -> None:
        try:
            self.rndis = RndisVideoReceiver(
                on_video=self._on_video,
                on_status=self.log_writer,
                on_stats=self._on_rndis_stats,
            )
            self.rndis.start()
        except Exception as exc:  # noqa: BLE001
            self.log(f"启动 RNDIS 接收失败：{exc}", "error")
            self._set_state("RNDIS 启动失败", RED)
            self._set_buttons(False)

    def _start_usb(self) -> None:
        self._stop_all_silent()
        self._new_decoder()
        self._reset_stats()
        self._open_recording()
        self.source_kind = "usb"
        self._begin_pipeline()
        self.receiver = MobileVideoReceiver(
            on_video=self._on_video,
            on_status=self.log_writer,
            on_stats=self._on_receiver_stats,
        )
        self.receiver.start()
        self.session_started = time.monotonic()
        self._set_state("手机模式连接中", AMBER)
        self._set_buttons(True)
        self.log("正在查找 2ca3:1002 手机模式设备...")

    def _start_udp(self) -> None:
        self._stop_all_silent()
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("端口错误", "UDP 端口必须是整数")
            return
        self._new_decoder()
        self._reset_stats()
        self._open_recording()
        self.source_kind = "udp"
        self._begin_pipeline()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_addr = self.bind_var.get().strip() or "127.0.0.1"
        try:
            sock.bind((bind_addr, port))
        except OSError as exc:
            sock.close()
            self._stop_decode_worker()
            messagebox.showerror("绑定失败", f"UDP {bind_addr}:{port} 绑定失败：{exc}")
            return
        sock.settimeout(0.3)
        self.udp_sock = sock
        self.udp_stop.clear()
        self.udp_thread = threading.Thread(target=self._udp_loop, args=(sock,), name="udp-h264", daemon=True)
        self.udp_thread.start()
        self.source_kind = "udp"
        self.session_started = time.monotonic()
        self._set_state(f"UDP 监听 {port}", AMBER)
        self._set_buttons(True)
        self.log(f"UDP 监听 {bind_addr}:{port}，等待裸 H.264 数据报。")

    def _udp_loop(self, sock: socket.socket) -> None:
        while not self.udp_stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if data:
                self._on_video(data)
                if not hasattr(self, "_udp_peer"):
                    self._udp_peer = addr
                    self.log_writer(f"收到来自 {addr[0]}:{addr[1]} 的视频数据")

    def _start_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Annex B H.264 文件",
            filetypes=[("H.264", "*.h264 *.264 *.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        self._start_file_path(path)

    def _start_file_path(self, path: str) -> None:
        self._stop_all_silent()
        self.source_var.set("本地 H.264 文件")
        self._new_decoder()
        self._reset_stats()
        self.raw_path = None
        self.source_kind = "file"
        self.file_stop.clear()
        self.file_thread = threading.Thread(target=self._file_loop, args=(path,), name="file-h264", daemon=True)
        self.file_thread.start()
        self.session_started = time.monotonic()
        self._set_state("文件解码", ACCENT)
        self._set_buttons(True)
        self.log(f"解码文件 {path}")

    def _file_loop(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                while not self.file_stop.is_set():
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    self.decoder.feed(chunk)
                    time.sleep(0.01)
            self.decoder.flush()
            self.log_writer("文件读取结束")
        except Exception as exc:  # noqa: BLE001
            self.log_writer(f"文件读取失败：{exc}")

    def _on_video(self, data: bytes) -> None:
        self.total_video_bytes += len(data)
        if self.rtsp:
            try:
                self.rtsp.push(data)
            except Exception:  # noqa: BLE001
                pass
        if self.raw_file is not None:
            try:
                self.raw_file.write(data)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.decode_queue.put_nowait(data)
        except queue.Full:
            # Keep the newest frame; count the drop so the UI can show it.
            self.decode_drops += 1
            try:
                self.decode_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.decode_queue.put_nowait(data)
            except queue.Full:
                self.decode_drops += 1

    def _on_receiver_stats(self, stats: ReceiverStats) -> None:
        if stats.connected and self.receiver and self.receiver.running:
            self.state_queue.put(("手机模式收流中", GREEN))
        if stats.last_error:
            self.log_writer(f"接收器：{stats.last_error}")

    def _on_rndis_stats(self, stats: RndisStats) -> None:
        if stats.connected and self.rndis and self.rndis.running:
            self.state_queue.put(("LiveView 收流中", GREEN))
        if stats.last_error:
            self.log_writer(f"RNDIS：{stats.last_error}")

    def stop_source(self) -> None:
        self._stop_all_silent()
        self._set_state("空闲", "#484f58")
        self._set_buttons(False)
        self.log("已停止。")

    def _stop_all_silent(self) -> None:
        self.rndis_prepare_stop.set()
        self._stop_decode_worker()
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
        if self.rndis:
            self.rndis.stop()
            self.rndis = None
        self.udp_stop.set()
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:  # noqa: BLE001
                pass
            self.udp_sock = None
        if self.udp_thread:
            self.udp_thread.join(timeout=1)
            self.udp_thread = None
        self.file_stop.set()
        if self.file_thread:
            self.file_thread.join(timeout=1)
            self.file_thread = None
        self._close_recording()

    def _set_buttons(self, running: bool) -> None:
        self.connect_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.source_combo.configure(state="disabled" if running else "readonly")
        self.decoder_combo.configure(state="disabled" if running else "readonly")

    def _reset_stats(self) -> None:
        self.last_stats = time.monotonic()
        self.last_frames = 0
        self.last_video_bytes = 0
        self.total_video_bytes = 0
        self._last_error_count = 0
        self.fps = 0.0
        self.bitrate_kbps = 0.0
        self.decode_ms = 0.0
        self.render_ms = 0.0
        self.displayed = 0
        self.last_frame = None
        self.rec_label.configure(text="")
        self.canvas.delete("all")
        self.canvas.create_text(
            max(10, self.canvas.winfo_width() // 2),
            max(10, self.canvas.winfo_height() // 2),
            text="正在连接 / 等待数据…",
            fill="#30363d",
            font=("Segoe UI", 16),
            justify="center",
            tags="hint",
        )

    # ------------------------------------------------------------- recording
    def _open_recording(self) -> None:
        self._close_recording()
        if not self.record_var.get():
            return
        outdir = ROOT / "captures"
        outdir.mkdir(exist_ok=True)
        self.raw_path = outdir / f"g3-{time.strftime('%Y%m%d-%H%M%S')}.h264"
        self.raw_file = self.raw_path.open("wb")
        self.rec_label.configure(text=f"录制：{self.raw_path.name}")
        self.log(f"开始录制原始流：{self.raw_path}")

    def _close_recording(self) -> None:
        if self.raw_file:
            try:
                self.raw_file.flush()
                self.raw_file.close()
            except Exception:  # noqa: BLE001
                pass
            self.raw_file = None
            if self.raw_path:
                self.log(f"录制已保存：{self.raw_path}")
                self.rec_label.configure(text=f"已保存：{self.raw_path.name}")

    # ------------------------------------------------------------- rendering
    def _pump(self) -> None:
        while not self.log_queue.empty():
            self._write_log(self.log_queue.get_nowait())
        while not self.diag_queue.empty():
            block = self.diag_queue.get_nowait()
            self.diag_text.configure(state="normal")
            self.diag_text.insert("end", block + "\n")
            self.diag_text.see("end")
            self.diag_text.configure(state="disabled")
        while not self.state_queue.empty():
            state, color = self.state_queue.get_nowait()
            if state == "__start_rndis_receiver__":
                self._begin_rndis_receiver()
                continue
            if state == "__enable_connect__":
                self._set_buttons(False)
                continue
            if color:
                self._set_state(state, color)
            else:
                self._set_state(state, "#484f58")
        frames = self.decoder.frames()
        if frames:
            self.last_frame = frames[-1]
            self.displayed += len(frames)
            self._render(self.last_frame)
        self.after(10, self._pump)

    def _render(self, frame: np.ndarray) -> None:
        started = time.perf_counter()
        h, w = frame.shape[:2]
        cw, ch = max(self.canvas.winfo_width(), 2), max(self.canvas.winfo_height(), 2)
        scale = min(cw / w, ch / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        image = Image.fromarray(frame, "RGB").resize((nw, nh), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self.photo, anchor="center")
        self.res_label.configure(text=f"{w} × {h}")
        self.render_ms = (time.perf_counter() - started) * 1000

    def _redraw_last(self) -> None:
        if self.last_frame is not None:
            self._render(self.last_frame)

    def _stats_tick(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self.last_stats, 0.001)
        stats = self.decoder.stats
        frames_delta = stats.frames - self.last_frames
        self.fps = frames_delta / elapsed
        self.last_frames = stats.frames
        video_bytes = self.total_video_bytes
        if video_bytes >= self.last_video_bytes:
            self.bitrate_kbps = (video_bytes - self.last_video_bytes) * 8 / elapsed / 1000
            self.last_video_bytes = video_bytes
        self.fps_label.configure(text=f"{self.fps:4.1f} fps")
        self.br_label.configure(text=f"{self.bitrate_kbps:6.0f} kbps")
        if self.receiver:
            self.pkt_label.configure(
                text=f"链路 {self.receiver.stats.link_packets} 包 · 视频 {self.receiver.stats.video_packets} 包"
            )
        elif self.rndis:
            self.pkt_label.configure(
                text=(
                    f"LiveView {self.rndis.stats.video_frames} 帧 · "
                    f"{self.rndis.stats.keyframes} 关键帧 · {self.rndis.stats.datagrams} 包"
                )
            )
        else:
            self.pkt_label.configure(text=f"已显示 {self.displayed} 帧")
        self.last_stats = now
        decoder_kind = "HW" if self.decoder.hardware_active else "SW"
        queue_size = self.decode_queue.qsize()
        self.lat_label.configure(
            text=(
                f"{decoder_kind} 解码 {self.decode_ms:4.1f} ms · "
                f"渲染 {self.render_ms:4.1f} ms · 队列 {queue_size} · 丢 {self.decode_drops}"
                + (
                    f" · 缺 {self.rndis.stats.gaps} · 错 {self.decoder.stats.errors}"
                    if self.rndis
                    else ""
                )
            )
        )
        if stats.errors and stats.errors != self._last_error_count:
            self._last_error_count = stats.errors
            self.log(f"解码错误累计 {stats.errors}（{stats.last_error[:60]}）", "error")
        self.after(1000, self._stats_tick)

    def save_frame(self) -> None:
        if self.last_frame is None:
            messagebox.showinfo("没有画面", "当前还没有解码出画面。")
            return
        outdir = ROOT / "captures"
        outdir.mkdir(exist_ok=True)
        path = outdir / f"frame-{time.strftime('%Y%m%d-%H%M%S')}.png"
        Image.fromarray(self.last_frame, "RGB").save(path)
        self.log(f"截图已保存：{path}")
        messagebox.showinfo("已保存", str(path))

    # ------------------------------------------------------------- diagnostics
    def run_diag(self) -> None:
        self.diag_text.configure(state="normal")
        self.diag_text.insert("end", "\n>>> 正在扫描...\n")
        self.diag_text.see("end")
        self.diag_text.configure(state="disabled")
        threading.Thread(target=self._diag_worker, name="diag", daemon=True).start()

    def _diag_worker(self) -> None:
        try:
            text = describe_pc_mode()
        except Exception as exc:  # noqa: BLE001
            text = f"扫描失败：{exc}"
        self.diag_queue.put(text)

    def check_mobile(self) -> None:
        from mobile_receiver import probe_mobile_mode

        self.diag_text.configure(state="normal")
        self.diag_text.insert("end", "\n>>> 检查手机模式...\n")
        self.diag_text.configure(state="disabled")
        self.diag_queue.put(probe_mobile_mode())

    def check_rndis(self) -> None:
        self.diag_text.configure(state="normal")
        self.diag_text.insert("end", "\n>>> 检查 LiveView / RNDIS...\n")
        self.diag_text.configure(state="disabled")
        threading.Thread(target=lambda: self.diag_queue.put(probe_rndis()), name="probe-rndis", daemon=True).start()

    def clear_diag(self) -> None:
        self.diag_text.configure(state="normal")
        self.diag_text.delete("1.0", "end")
        self.diag_text.configure(state="disabled")

    def copy_rtsp(self) -> None:
        url = self.rtsp.url if self.rtsp else "rtsp://127.0.0.1:8554/fpv_stream"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.log(f"已复制 Mission Planner RTSP：{url}")

    def open_zadig(self) -> None:
        candidates = [
            ROOT / "vendor" / "zadig-2.9.exe",
            pathlib.Path(r"D:\zadig2.9\zadig-2.9.exe"),
        ]
        for path in candidates:
            if path.exists():
                import os

                os.startfile(str(path))  # noqa: S606
                return
        messagebox.showinfo(
            "Zadig",
            "未找到 zadig-2.9.exe。\n手机模式下需要给 Interface 1 安装 WinUSB 或 libusbK 驱动。",
        )

    # ------------------------------------------------------------- close
    def _on_close(self) -> None:
        try:
            self._stop_all_silent()
            if self.rtsp:
                self.rtsp.stop()
        finally:
            self.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="G3Viewer")
    parser.add_argument("--udp", type=int, help="启动后直接监听 UDP 裸 H.264 端口")
    parser.add_argument("--bind", default="127.0.0.1", help="UDP 监听地址，局域网桥接用 0.0.0.0")
    parser.add_argument("--file", help="启动后直接解码一个 H.264 文件")
    parser.add_argument("--rndis", action="store_true", help="启动后直接连接 USB LiveView")
    parser.add_argument("--hardware", action="store_true", help="使用 D3D11VA 硬件解码")
    parser.add_argument("--diag", action="store_true", help="只打印 PC/OTG USB 诊断，不打开窗口")
    args = parser.parse_args()
    if args.diag:
        text = describe_pc_mode()
        print(text)
        if getattr(sys, "frozen", False):
            try:
                (ROOT / "diag.txt").write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        return 0
    app = G3Viewer(args)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
