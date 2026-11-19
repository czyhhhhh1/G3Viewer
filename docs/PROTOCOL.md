# DJI Goggles 3 USB 协议笔记

本文记录 2026-10-25 在本机（Windows 11，Goggles 3，序列号
`987654321ABCDEF`）上的实测，以及公开逆向资料的对照。只描述“怎么识别、
怎么解包、哪些路走不通”，不包含 DJI 固件或密钥。

## 1. 设备模式总览

| 模式 | VID:PID | 主机看到的东西 | 视频 |
|---|---|---|---|
| PC / OTG | `2ca3:0020` | RNDIS + U 盘 + 5 个 `ff/43/01` Bulk | 无 |
| 手机 / MFI | `2ca3:1002` | iAP 接口 + `com.dji.logiclink` alt 1 | 有，H.264 |
| Android 配件 | 取决于设备 | AOA `com.dji.logiclink` | 有（手机端） |

Goggles 3 的 USB-C 是 USB 2.0（手册：480 Mbps，最大输入 9 V/3 A），
所以两种模式都不会出现 USB 3 SuperSpeed 端点。

## 2. PC / OTG 模式（2ca3:0020）

### 2.1 设备与配置

设备描述符：

```text
bcdUSB      0x0210
idVendor    0x2ca3
idProduct   0x0020
bcdDevice   0x0504
iManufacturer 1  DJI
iProduct      2  Goggles3-753XM5L7025622
iSerial       3  987654321ABCDEF
bNumConfigurations 1
```

配置字符串（iConfiguration=4）：

```text
rndis,mass_storage,bulk,acm
```

Windows 侧 8 个接口：

| MI | class/sub/proto | 角色 | 端点 |
|---|---|---|---|
| 00 | `e0/01/03` | RNDIS 控制 | IN `0x82` |
| 01 | `0a/00/00` | RNDIS/CDC 数据 | IN `0x81`, OUT `0x01` |
| 02 | `08/06/50` | Mass Storage | IN `0x83`, OUT `0x02` |
| 03 | `ff/43/01` | 厂商 Bulk 3 | OUT `0x03`, IN `0x84` |
| 04 | `ff/43/01` | 厂商 Bulk 4（DUML 状态） | OUT `0x04`, IN `0x85` |
| 05 | `ff/43/01` | 厂商 Bulk 5 | OUT `0x05`, IN `0x86` |
| 06 | `ff/43/01` | 厂商 Bulk 6 | OUT `0x06`, IN `0x87` |
| 07 | `ff/43/01` | 厂商 Bulk 7 | OUT `0x07`, IN `0x88` |

- 所有端点都是 Bulk，`wMaxPacketSize=512`，没有 Isochronous；
- 没有 `bInterfaceClass=0x0e`（UVC）接口；
- 没有视频 IN 端点，所以标准摄像头 API、OBS、ffmpeg dshow 都找不到它。

> 截图里 Zadig 下拉菜单出现的 `Integrated Camera`、`USB Gaming Keyboard`、
> `BT_FUNCTION` 是电脑自带的摄像头/键盘/蓝牙，不是眼镜。判断依据是它们的
> VID/PID 和接口号与 `2ca3:0020` 不一致。

### 2.2 MI04 心跳

MI04 每秒主动发一包 77 字节数据（`usb_sniff_libusb0.py` 被动抓）：

```text
55 4d 04 a8 bc 2a b3 03 40 00 81 5a 56 39 30 32
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 05 1c 00 00 00
00 00 00 05 1c 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 93 a5
```

结构：

| 偏移 | 值 | 含义 |
|---|---|---|
| 0 | `0x55` | DUML 同步字 |
| 1 | `0x4d` | 整包长度 77 |
| 2 | `0x04` | DUML 版本 |
| 3 | `0xa8` | 头部 CRC8（对 `55 4d 04`，seed 0x77） |
| 4–5 | `bc 2a` | 固定，疑似发送/接收地址或会话 |
| 6–7 | `b3 03` … | 16 位序号，逐包 +1/+2 |
| 8 | `0x40` | cmd_set |
| 9–10 | `00 81` | cmd_id（大端表示 0x0081） |
| 11–15 | `ZV902` | 型号/组件标识 ASCII |
| … | | 状态字段，含两个 `0x1c05` |
| 75–76 | `93 a5` | CRC16，小端 |

CRC16 算法：反射多项式 `0x8408`，seed `0x3692`，对前 75 字节计算，
结果 `0xa593` 以小端存放。代码见 `tools/duml.py`。

### 2.3 写了什么、为什么没视频

对 5 个 Bulk 接口做了受控测试：

| 接口 | 写 OUT | 结果 |
|---|---|---|
| MI03 | `0x03` | `usb_bulk_write` timeout |
| MI04 | `0x04` | 写入成功；有时在 IN 端回显同一包；普通 Ping/Version 无响应 |
| MI05 | `0x05` | timeout |
| MI06 | `0x06` | timeout |
| MI07 | `0x07` | timeout |

在 MI04 上尝试过：

- 裸 `RMVT`（0x52 4d 56 54）；
- 标准 DUML Ping `00 00`、Version `00 01`；
- 地址组合 `0x2a→0xbc`、`0xbc→0x2a`、`0x03→0xb3`、`0xf3→0xfe` 等；
- 字段顺序 `seq→addr` 与 `addr→seq` 两种；
- 公开的 Goggles N3 两包开流命令（`40 00 99` / `40 00 88`），按 PC 模式
  重算 DUML 头和 CRC 后发到 MI04。

结果都只有原来的 77 字节心跳，没有 ACK、错误包或视频数据。说明当前固件的
PC 模式不提供视频处理路径，而不是命令“差一个字节”。

### 2.4 官方手册的表述

Goggles 3 用户手册（v1.0，2024-05-28）中：

- USB-C 规格：USB 2.0，最大输入 9 V/3 A；
- PC 连接只出现在“导出素材/升级”：
  - “使用 USB-A 转 USB-C 数据线连接飞行眼镜的 USB-C 接口至个人电脑”；
  - “在眼镜菜单中选择设置 > 关于，进入 OTG 有线连接模式导出素材”；
- 视频分享只有两种：
  - “有线连接手机”：手机数据线 + 标配 USB-C OTG 转接线；
  - “无线连接手机”：Wi-Fi 共享眼镜画面到手机。

没有“USB 摄像头/PC 视频输出”这一项。**但 LiveView sharing 会复用同一条
RNDIS 链路传视频**，这是本机 2026-10-25 实测确认的。

### 2.5 LiveView sharing：PC 视频的真实通道

条件：

1. 眼镜已连接天空端/飞行器，屏幕有图传；
2. 设置 > 关于 > **OTG 有线连接模式**；
3. 5D 键向后 > **LiveView sharing / 实时画面共享到手机**；
4. USB-C 数据线接 PC。

网络：

```text
host  192.168.60.1/24   (Windows RNDIS adapter)
peer  192.168.60.2:9003 (goggles)
local UDP 12346
```

Windows 首次连接需要管理员给 RNDIS 网卡加 `192.168.60.1/24`；不要用 Zadig
替换 RNDIS 驱动。

握手（48 字节）：

```text
30 80 <session:2 LE> 00 00 00 00
D0 E9 64 00 64 00 C0 05 14 00 00 0A 00 64 00 64 00
C0 05 14 00 00 64 00 14 00 64 00 C0 05 14 00 00 64 00
01 01 04 0A 02
```

- session 随机；
- 第 7 字节 = 前 7 字节 XOR；
- 每 1–2 秒重发；即使已出画面也建议保持，否则部分固件会停流。

UDP 包：

```text
0..1  (payload len | 0x8000)
2..3  session
4..5  sequence
6     type: 0=握手确认, 2=视频, 4=ACK
7     XOR of bytes 0..6
```

视频：

```text
17..19  part/count
20..    H.264 分片
```

- `count = data[17] & 0x7f`
- `part = (data[17] >> 7) | (data[18] << 1) | (data[19] << 9)`
- `key = (sequence & 0xfff8) - part*8`
- 收齐后按 part 顺序拼接；最后一包回 30 字节 ACK（type 4，session 在 2..3，
  sequence 在 8..11）。

**关键帧：** 眼镜使用 intra-refresh，不保证新 UDP 会话从 IDR 开始。
PC 端必须从 SPS/PPS+IDR 起播；如果连接后只收到 P 帧，保持程序连接，
在眼镜上关/开一次 LiveView sharing，让它重发关键帧。实测关键帧前会有
一个只含 SPS/PPS 的 40 字节帧，不能把它清掉。

本机实测：1920×1080、30 fps、约 10 Mbps。

## 3. 手机 / MFI 模式（2ca3:1002）

以下结构来自公开的 Goggles N3 实测（samuelsadok/dji_protocol），Goggles 3
与其同代，接口名和逻辑链路格式一致；具体开流命令需在 Goggles 3 上复核。

### 3.1 描述符

```text
idVendor  0x2ca3
idProduct 0x1002
bcdDevice 5.04
Configuration: bNumInterfaces 2, iConfiguration "MFI"
Interface 0: ff/f0/00  "iAP Interface"     EP 0x81 IN / 0x01 OUT
Interface 1 alt 0: ff/f0/01 "com.dji.logiclink"  无端点
Interface 1 alt 1: ff/f0/01 "com.dji.logiclink"  EP 0x82 IN / 0x02 OUT
```

Android 端 APK 里还能看到 AOA 配件过滤：

```text
manufacturer DJI
model        WM160
com.dji.link
com.dji.logiclink
usb-accessory
```

### 3.2 逻辑链路封装

```text
offset 0..1  55 CC
offset 2..3  port，小端
offset 4..5  payload length，小端（不含 8 字节头）
offset 6..7  00 00
offset 8..   payload
```

一个 USB transfer 可以包含多个封装包，也可以把一个包拆开；必须做流式重组。

### 3.3 端口

| 端口 | 十进制 | 方向 | 内容 |
|---|---|---|---|
| `0x574A` | 22346 | 设备→主机 | 裸 H.264（Annex B） |
| `0x4957` | 22345 | 主机→设备 | DUML 命令 |
| `0x7530` | 30000 | 设备→主机 | DUML 状态/遥测 |

### 3.4 开流命令

两包，必须都发；每 5 秒重发可维持。以下为 Goggles N3 的原始 USB transfer
（包含 8 字节 0x55CC 头和 2 字节封装 CRC）：

```text
# 1) 40 00 99 "camcap_common"
55 cc 49 57 2d 00 00 00
55 2d 04 f2 02 28 f3 fe 40 00 99
02 02 00 00 d5 07 00 00 00 00 00 13 00 0d 00
63 61 6d 63 61 70 5f 63 6f 6d 6d 6f 6e 00 00 00 00 d0 93
92 3a

# 2) 40 00 88 "APP"
55 cc 49 57 1b 00 00 00
55 1b 04 75 02 3c f4 fe 40 00 88
17 00 00 23 00 41 50 50 00 00 00 00 00 02 58 a6
34 18
```

注意：第一段里 `55 2d` 的 DUML 长度是 45，DUML CRC 是 `d0 93`；末尾
`92 3a` 是外层封装 CRC，不是 DUML CRC。公开发文档的表格容易看错这一点。

### 3.5 保活

- 命令 1 每秒重发一次，payload 第 5 字节（`d5`）递增；
- 命令 2 不重发；随后设备每秒发一包 `00 88`，主机回
  `1a 00 00 00 00`；
- 偷懒做法：两包一起每 5 秒重发，实测 N3 可无限维持。

## 4. 为什么 PC 不能直接进手机模式

USB 角色由端口上的 CC/ID 和主机枚举行为决定：

1. 眼镜作为 Host 枚举对端；
2. 对端是 iPhone 时，眼镜发 vendor request `0x51`，随后双方换角色；
3. 对端是 Android 时，走 AOA 配件模式，手机作为 device 暴露
   `com.dji.logiclink`；
4. 对端是 PC 时，PC 只做 Host，眼镜按 PC 模式枚举成 `2ca3:0020`。

PC 的 USB-C 口通常是 host-only，Windows 也没有 USB device/gadget 栈，所以
无法让眼镜把它当手机。Cynthion 的办法是：先模拟 iPhone 完成 0x51 握手，
然后从总线上“让开”，让眼镜直接枚举到 PC；树莓派的办法是：USB-C 口先做
gadget 模拟手机，收流后再通过网络把 H.264 转发给 PC。

## 5. 复现实验

```powershell
# 描述符
python tools\usb_probe.py
python tools\usb_descriptors.py

# 被动抓 MI03..MI07 的 IN
python tools\usb_sniff_libusb0.py --seconds 8

# 只看 MI04 心跳并解析
python -c "import sys; sys.path.insert(0,'app'); from pc_diag import describe_pc_mode; print(describe_pc_mode())"

# DUML 组包/校验
python tools\duml.py

# 把 N3 开流命令打到 PC 模式 MI04（无视频，预期只有心跳）
python tools\start_video_probe.py --interface 4 --seconds 14
```

## 6. 公开资料

- [DJI USB-to-mobile protocol](https://github.com/samuelsadok/dji_protocol/blob/master/usb_mobile_protocol.md)
- [video_out_mobile.py](https://github.com/samuelsadok/dji_protocol/blob/master/scripts/video_out_mobile.py)
- [emulate_iphone.py](https://github.com/samuelsadok/dji_protocol/blob/master/scripts/emulate_iphone.py)
- [DJI proprietary UDP protocol](https://github.com/samuelsadok/dji_protocol/blob/master/udp_protocol.md)
- [DJI Goggles 3 用户手册](https://dl.djicdn.com/downloads/DJI_Goggles_3/20240528/DJI_Goggles_3_User_Manual_CHS.pdf)
- [Cosmostreamer Goggles 2/3](https://cosmostreamer.com/products/djigoggles2/)
- [DigiView-Android（V1/V2 手机收流，思路参考）](https://github.com/fpvout/DigiView-Android)
