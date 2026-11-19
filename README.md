# G3Viewer · DJI Goggles 3 USB 收流器

Windows 上直接通过 USB 看 DJI Goggles 3 的图传画面，不需要 Cynthion、不需要手机、不需要连眼镜 Wi-Fi。

![G3Viewer 实机画面](captures/gui-async-sw.png)

## 为什么之前没有画面

你的眼镜现在确实连着天空端，但 PC 侧只看到：

```text
2CA3:0020  rndis,mass_storage,bulk,acm
MI00 RNDIS / MI01 RNDIS 数据 / MI02 U 盘
MI03–MI07  5 个厂商 Bulk
MI04 每秒 77 字节 DUML 心跳：40 00 81 "ZV902" ...
```

这是 **PC/OTG 文件模式**，本来就没有视频。天空端只让眼镜屏幕有画面，不会自动把视频推到 Bulk 端点。

Goggles 3 还有另一个开关：**LiveView sharing / 实时画面共享**。开启后，视频走 **USB RNDIS 网卡上的 UDP**，不是 UVC，也不是 Bulk：

```text
电脑 RNDIS 网卡：192.168.60.1/24
眼镜：          192.168.60.2:9003
本地 UDP：      12346
```

所以正确步骤是：**OTG 有线连接 + LiveView sharing + 本程序**。本程序已经实现了这条 RNDIS 协议，并已在天空端实机验证：**1920×1080、30 fps、约 10 Mbps**。

> 眼镜使用 intra-refresh。程序连接后如果一直黑屏，请在**保持程序连接**时，
> 把 LiveView sharing 关掉再打开一次：眼镜会重新发 SPS/PPS + IDR 关键帧。
> 程序会持续握手等待，收到关键帧后立即出画面。
>
> 低延迟版实测：**解码约 5 ms · 渲染约 10 ms**（1920×1080 / 30 fps）。
> 应用侧管线约 15 ms；此前的默认 PyAV 帧级线程会缓存约 17 帧，约 0.53 s，
> 这是之前 0.8 s 延迟的主要来源。解码器可选软件或 D3D11VA 硬件。

---

## 1. 直接运行

```text
G3Viewer-win64.zip   解压后运行 G3Viewer.exe
```

也可以先只做诊断：

```powershell
.\G3Viewer.exe --diag
# 结果写到同目录 diag.txt
```

### 第一次连接（关键）

1. 天空端/飞行器开机，眼镜屏幕能看到图传；
2. 眼镜：**设置 > 关于 > OTG 有线连接模式**，打开；
3. 眼镜：按 **5D 键向后**，打开 **LiveView 共享 / 实时画面共享到手机**；
   - 名字里虽然写 Wi-Fi，但本程序走 USB，电脑不用加入眼镜 Wi-Fi；
4. USB-C 数据线接电脑；
5. 打开 G3Viewer，源选 **USB RNDIS · LiveView 共享**，点 **开始**；
6. **解码器**可选：
   - **软件 (CPU)**：兼容性最好，本机约 5 ms/帧；
   - **硬件 (D3D11VA)**：CPU 占用更低，本机约 4.5 ms/帧；不可用时自动回退软件；
7. 第一次会弹一次 UAC：只给 VID `2CA3:PID 0020` 的 RNDIS 网卡添加
   `192.168.60.1/24`，不改默认网关、DNS、防火墙；
8. 程序发 LiveView 握手，收到 H.264 后显示画面；
9. 如果日志停在“收到视频但无关键帧”，保持程序连接，把 LiveView 共享
   关掉再打开一次（眼镜会补发 SPS/PPS+IDR），画面随后出现。

如果 UAC 被拒绝，程序会停在“RNDIS 配置失败”。用管理员 PowerShell 手动执行：

```powershell
# 先找到眼镜网卡（Remote NDIS based Internet Sharing Device）
Get-NetAdapter | Where-Object InterfaceDescription -match 'Remote NDIS'

# 把 <ifIndex> 换成上面看到的数字
New-NetIPAddress -InterfaceIndex <ifIndex> -IPAddress 192.168.60.1 -PrefixLength 24
```

删除时：

```powershell
Remove-NetIPAddress -InterfaceIndex <ifIndex> -IPAddress 192.168.60.1 -PrefixLength 24
```

> 只改这一个 USB RNDIS 网卡，不要动 Wi-Fi/以太网，也不要给 RNDIS 装 Zadig/WinUSB；
> 它必须继续使用微软的 RNDIS 驱动。

---

## 2. 程序现在支持四种源

| 源 | 用途 |
|---|---|
| **USB RNDIS · LiveView 共享** | **推荐**。OTG + LiveView，直接 USB 收 H.264 |
| 手机模式 USB (2ca3:1002) | Cynthion/树莓派角色切换后的 MFI 路线，备用 |
| UDP 裸 H.264 | 接树莓派/Cosmostreamer 等网络桥，默认监听 127.0.0.1:5004 |
| 本地 H.264 文件 | 回放/验证解码器 |

### 本地验证播放器

不需要眼镜：

```powershell
# 终端 A
python app\main.py --udp 5004

# 终端 B
python tools\udp_h264_sender.py captures\synthetic.h264 --loop
```

局域网桥转发时用 `--bind 0.0.0.0`，首次监听会让 Windows 防火墙询问。

---

## 3. RNDIS / LiveView 协议

实现参考 MIT 项目 [knpuma/goggle-3-liveview](https://github.com/knpuma/goggle-3-liveview)，
并在 Python 里按它的协议向量做了单元测试。

### 3.1 网络

```text
host  192.168.60.1/24  (USB RNDIS adapter)
peer  192.168.60.2:9003
local UDP 12346
```

### 3.2 握手（48 字节）

```text
30 80 <session:2 LE> 00 00 00 00
D0 E9 64 00 64 00 C0 05 14 00 00 0A 00 64 00 64 00
C0 05 14 00 00 64 00 14 00 64 00 C0 05 14 00 00 64 00
01 01 04 0A 02
```

- `session` 随机；
- 第 7 字节 = 前 7 字节异或；
- 每 1 秒重发，直到收到第一帧。

### 3.3 视频包

UDP 头前 8 字节：

```text
0..1  (len | 0x8000)
2..3  session
4..5  sequence
6     type (0 = 握手确认, 2 = 视频, 4 = ACK)
7     XOR of bytes 0..6
```

视频包：

```text
17..19  part/count 编码
20..    H.264 分片
```

- `count = data[17] & 0x7f`
- `part = (data[17] >> 7) | (data[18] << 1) | (data[19] << 9)`
- 同一帧按 `key = (sequence & 0xfff8) - part*8` 重组；
- 最后一包回 30 字节 ACK（type 4，session 在 2..3，sequence 在 8..11）；
- 载荷是 Annex B H.264，PyAV 直接解码。

### 3.4 和手机/MFI 的区别

| 路线 | 设备 | 传输 |
|---|---|---|
| LiveView / PC | `2CA3:0020` RNDIS | UDP 192.168.60.2:9003 |
| 手机/MFI | `2CA3:1002` `com.dji.logiclink` | 0x55CC 逻辑链路，端口 22346 |
| 旧 PC 文件模式 | `2CA3:0020` Bulk | 只有 DUML 心跳，无视频 |

### 3.5 低延迟优化（0.8s → 应用侧约 15ms）

实测同一段 1080p H.264，用 PyAV 默认配置：

```text
thread_type=AUTO：首帧前缓存 17 帧，最大积压 16 帧 → 30fps 约 530ms
thread_type=NONE：首帧前 2 帧，最大积压 1 帧
整帧直喂解码：   首帧 1 帧，最大积压 0 帧
```

现在 `app/decoder.py` 使用：

```python
thread_type = "NONE"
thread_count = 1
flags |= AV_CODEC_FLAG_LOW_DELAY
```

并且软件解码路径把 LiveView 的完整 access unit 直接
`decode(av.Packet(frame))`，不再经过 `parse()` 的额外一帧缓存；硬件路径为了
多 slice 兼容性走标准 parser。本机实测：

```text
解码约 5 ms · 渲染约 10 ms · 1920×1080 / 30 fps / 约 10 Mbps
```

剩余延迟主要是眼镜编码、天空端无线链路和 LiveView 本身；PC 侧已经不再
积压帧。底部状态栏会实时显示“解码 / 渲染”耗时，方便判断。

### 3.6 抗马赛克与软/硬件解码

运动时的高码率会让“收包线程里直接解码”短暂停止收包，丢掉分片后 P 帧参考
断裂，表现就是马赛克。现在改成：

- **收包与解码分离**：UDP 线程只做重组和入队，独立解码线程消费；
- 解码队列上限 30 帧，保留最新帧；队列满才丢帧并计数；
- UDP 接收缓冲从 2 MB 提到 8 MB，吸收运动码率突发；
- 底部显示 `队列 / 丢 / 缺 / 错`：
  - `丢`：解码队列满导致的帧丢弃；
  - `缺`：UDP 序号跳变（网络分片丢失）；
  - `错`：libavcodec 解码错误。

本机运动测试 2364 帧：

```text
队列 0 · 丢 0 · 缺 0 · 错 4（启动时留下，未增长）
```

解码器下拉框：

| 选项 | 实现 | 本机耗时 |
|---|---|---:|
| 软件 (CPU) | PyAV/libavcodec 单线程低延迟，整帧直喂 | 约 5 ms/帧 |
| 硬件 (D3D11VA) | PyAV + D3D11VA，走标准 H.264 parser | 约 3–5 ms/帧 |

硬件解码不可用（无 D3D11、驱动异常）时自动回退软件并在日志提示。
硬件路径不再使用整帧直喂：D3D11VA 对多 slice 的 access unit 更严格，直喂会在
运动时出现马赛克；现在硬件路径走 parser，软件路径仍保留零额外帧延迟的直喂。
命令行等价开关：

```powershell
G3Viewer.exe --rndis --hardware
```

---

### 3.7 输出到 Mission Planner

程序启动时会自动开一个低延迟 RTSP 服务：

```text
rtsp://127.0.0.1:8554/fpv_stream
```

它把收到的**原始 H.264** 直接打成 RTP，不重新编码，所以附加延迟只有
RTSP/RTP 和 Mission Planner 自己的解码。USB 诊断页有“复制 Mission Planner RTSP”。

Mission Planner 设置：

1. 右键 HUD → **Video → Set GStreamer Source**；
2. 粘贴低延迟 pipeline：

```text
rtspsrc location=rtsp://127.0.0.1:8554/fpv_stream latency=1 protocols=udp udp-reconnect=1 timeout=0 do-retransmission=false ! application/x-rtp,media=video,encoding-name=H264 ! rtph264depay ! h264parse ! queue max-size-buffers=1 leaky=2 ! avdec_h264 ! queue max-size-buffers=1 leaky=2 ! videoconvert ! video/x-raw,format=BGRA ! appsink name=outsink sync=false
```

3. 需要 Mission Planner 的 GStreamer 运行时；若 MP 提示未安装，让它下载/安装后重启；
4. 如果 Mission Planner 画面黑屏，先在 G3Viewer 里确认有画面，再在 MP 连接后
   关/开一次 LiveView 共享（让 RTSP 客户端也拿到关键帧）。

RTSP 服务只监听 `127.0.0.1`，不会暴露到局域网。VLC 也可以直接打开这个地址
做验证；VLC 默认 UDP，必要时把 `--rtsp-tcp` 关掉。

RTSP 的 NAL 扫描和 RTP 打包在**独立 feed 线程**里，不在收包/解码线程；没有
RTSP 客户端时，缓存好 SPS/PPS/IDR 后直接跳过每帧解析。所以不开 Mission
Planner 时，这个功能不会拖慢本地解码。若本地 G3Viewer 出现卡顿，先看底部
`队列/丢/缺/错`；正常应全为 0（`错` 可能保留启动时的 1–4）。

### 3.8 Mission Planner 卡在下载 GStreamer

Mission Planner 是 32 位程序，需要 **x86 GStreamer**；它自带的下载器访问 GitHub
经常一直卡在 “downloading”。本机已改成便携方案，不需要管理员安装：

```text
D:\MissionPlanner-stable\gstreamer\1.0\x86
  -> D:\g3\vendor\gstreamer-x86\gstreamer\1.0\msvc_x86   (目录联接)
D:\MissionPlanner-stable\run_mission_planner_with_gst.cmd
```

同时已在 Mission Planner 的
`C:\Users\<用户>\Documents\Mission Planner\config.xml` 写入：

```xml
<gstlaunchexe>D:\MissionPlanner-stable\gstreamer\1.0\x86\bin\gst-launch-1.0.exe</gstlaunchexe>
```

这样 MP 不会再弹下载，直接使用本地 `gst-launch-1.0`。如果 MP 升级后配置被重置，
重新写入这一行，或用 `run_mission_planner_with_gst.cmd` 启动。

GStreamer 包本身在：

```text
D:\g3\vendor\gstreamer-1.0-msvc-x86-1.24.13.msi
```

已经用 `msiexec /a` 解压，不需要安装；`gst-inspect-1.0 avdec_h264` 和
`gst-launch-1.0` 的 RTSP 管线都已验证通过。

## 4. 常见问题

**Q：天空端已连，为什么还是没画面？**
A：先确认眼镜里 **LiveView sharing** 已打开。只开 OTG 不够；只连天空端也不够。
打开后拔插一次 USB 线，再点开始。

**Q：UAC 一定要给吗？**
A：只有第一次需要。它只给眼镜 RNDIS 网卡加 `192.168.60.1/24`。不加这个地址，
Windows 无法把 UDP 包送到 `192.168.60.2`。

**Q：能装 Zadig 吗？**
A：RNDIS 网卡不能。Zadig 只用于手机/MFI 模式的 `2ca3:1002` Interface 1；
PC/LiveView 模式必须保留微软 RNDIS 驱动。

**Q：LiveView 是 Wi-Fi 共享，电脑要连眼镜 Wi-Fi 吗？**
A：不用。开关名字是 DJI 的历史命名，本程序走 USB RNDIS。电脑的 Wi-Fi 可以保持原样。

**Q：画面卡/等待视频？**
A：关闭其他占用眼镜 USB 或 12346 端口的程序；拔插 USB；重新开关 LiveView。
如果握手确认但无视频，检查天空端是否真的在图传、眼镜是否在录像/回放页面。

**Q：延迟还是比眼镜原生显示大？**
A：先看底部“解码 / 渲染”两个数。正常应约 5 ms / 10 ms；如果它们很大，
说明 PC 侧卡顿（关掉录制、其他解码器或降低窗口尺寸）。如果它们很小但体感
仍慢，剩余部分是眼镜编码、天空端无线链路和 LiveView 共享本身，PC 端已经
没有帧积压，无法再靠软件消除。实测应用侧从约 530 ms 降到约 15 ms。

**Q：Mission Planner 里没有画面？**
A：确认 G3Viewer 自己已经有画面；确认 MP 已安装 GStreamer；用 3.7 的完整
pipeline，而不是只填 URL。MP 连接后再关/开一次 LiveView，确保它从 IDR 开始。
本机已用 FFmpeg 作为 RTSP 客户端验证 12 帧解码通过。

**Q：录制文件在哪？**
A：勾选“录制原始 H.264”后写到 `captures/g3-*.h264`。转 MP4：
`ffmpeg -framerate 60 -i captures/g3-xxxx.h264 -c copy out.mp4`。

---

## 5. 文件结构

```text
app/
  main.py             GUI、RNDIS/LiveView、手机模式、UDP、文件、录制
  rndis_receiver.py   LiveView 握手/组帧/ACK + 192.168.60.1 配置
  rtsp_server.py      低延迟 RTSP/RTP H.264 输出（Mission Planner）
  mobile_receiver.py  2ca3:1002 0x55CC 逻辑链路（备用）
  decoder.py          PyAV H.264 解码
  pc_diag.py          PC/OTG 描述符与 MI04 心跳诊断
tools/
  test_rndis_protocol.py   LiveView 协议向量测试
  test_decoder.py          H.264 解码测试
  test_rtsp.py             RTSP 服务 + FFmpeg 拉流测试
  bench_buffer.py          解码器首帧/积压对比（17 帧 vs 1 帧）
  bench_decode.py          1080p 解码吞吐测试
  usb_probe.py / usb_sniff_libusb0.py / usb_send.py / duml.py
  udp_h264_sender.py       本地 UDP 测试发送器
  rndis_uac_test.cmd/.ps1  老的手动 RNDIS 测试（可不用）
docs/
  PROTOCOL.md         描述符、DUML、LiveView、MFI 协议
captures/             截图、合成 H.264
```

---

## 6. 已验证 / 已知限制

**已在天空端实机验证**

- PC/OTG 描述符、8 个接口、MI04 心跳；
- 天空端连接后 Bulk 仍然没有视频（证明必须走 LiveView/RNDIS）；
- RNDIS `192.168.60.1/24` + `192.168.60.2:9003` 收流；
- **1920×1080、30 fps、约 10 Mbps 的画面**；
- 低延迟模式：解码约 5 ms、渲染约 10 ms，应用侧管线约 15 ms；
- 解码器首帧从 17 帧降到 1 帧，最大积压从 16 帧降到 0 帧；
- 软件解码和 D3D11VA 硬件解码都实机出画面；
- 硬件路径改为 parser 输入后，运动马赛克消失（用户实机确认）；
- 运动测试 2364 帧：队列 0、丢 0、缺 0；
- RTSP 输出经 FFmpeg 客户端连续拉流 40 帧解码通过；
- 解码、显示、帧率/码率/队列/缺包/错误统计；
- RNDIS/LiveView 协议与 MIT 项目的测试向量逐字节一致；
- 打包后的 exe 能启动、诊断、收流。

**已知限制：关键帧**

眼镜使用 intra-refresh，不保证每个 UDP 会话一开始就有关键帧。若连接后一直
黑屏、但“LiveView 收流中”且码率非零，请保持程序连接，在眼镜上把 LiveView
sharing 关掉再打开一次。程序日志会依次出现：

```text
LiveView 会话已确认，等待视频帧...
收到 IDR 关键帧，开始解码。
```

之后就是正常画面。若 12 秒仍无关键帧，程序会主动提示。

---

## 7. 合规与致谢

本项目只做自己设备上的互操作/协议研究，不包含 DJI 固件、签名密钥或破解授权代码。
使用前请确认不违反当地法律、DJI 用户协议和保修条款；不要用于规避飞行限制、
窃取他人图传或干扰无线电。

RNDIS/LiveView 协议实现参考：

- [knpuma/goggle-3-liveview](https://github.com/knpuma/goggle-3-liveview)（MIT）
- [Windows/README.md](https://github.com/knpuma/goggle-3-liveview/blob/main/Windows/README.md)
- [LiveProtocol.cs](https://github.com/knpuma/goggle-3-liveview/blob/main/Windows/Goggles3.Core/LiveProtocol.cs)

手机/MFI 备用协议参考 [samuelsadok/dji_protocol](https://github.com/samuelsadok/dji_protocol)。
