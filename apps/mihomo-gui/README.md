# Clash Fuxi —— 手机原生 Clash/Mihomo 图形客户端

面向 Xiaomi 13 `fuxi` 主线 Linux + Phosh（aarch64 Ubuntu 26.10）的原生代理客户端，
作用是手机系统上的 “Clash for Android 等价物”。

它不是安卓 APK，而是用 **GTK4 + libadwaita + PyGObject** 写的原生 Linux 应用，
内核用 **Mihomo（Clash.Meta）** 官方 `linux-arm64` 静态二进制。界面贴合 Phosh 手势与小屏。

## 组成

| 组件 | 位置（手机） | 说明 |
|---|---|---|
| Mihomo 内核 | `/usr/local/bin/mihomo` | v1.19.33 官方 aarch64 静态二进制，已 `setcap cap_net_admin,cap_net_raw+ep` |
| GUI 应用 | `/usr/local/bin/clash-fuxi` | 本目录 `clash_fuxi.py` |
| 启动器 | `/usr/share/applications/clash-fuxi.desktop` | 出现在 Phosh 应用列表 |
| 配置 | `~/.config/mihomo/config.yaml` | 订阅/节点/规则 |
| 运行态 | `~/.local/state/clash-fuxi/` | pid、日志 |

## 功能

- 内核启停开关（无需 root；靠 setcap 授权 TUN）
- 订阅链接导入（粘贴 http/https Clash 订阅 → 自动下载并写 config.yaml）
- **TUN 透明代理**开关（整机流量走代理）
- 系统 HTTP/SOCKS 代理开关（GNOME gsettings，`127.0.0.1:7890`）
- 代理模式：规则 / 全局 / 直连
- 实时上传/下载速率与连接数
- 内核日志查看

## 依赖（手机需已安装）

```
gir1.2-gtk-4.0  gir1.2-adw-1  gir1.2-graphene-1.0   # 已装
nftables                                            # TUN auto-route 需要，已装
```

## 关键前提：内核需支持策略路由 + nft 对象

主线内核默认配置缺以下选项，会导致 TUN 报
`add rule ... operation not supported` / `cleanup rules: operation not supported`：

```
CONFIG_IP_ADVANCED_ROUTER=y      # 策略路由（ip rule）
CONFIG_IP_MULTIPLE_TABLES=y      # 多路由表 / fwmark 表
CONFIG_NFT_REDIR=y
CONFIG_NF_TABLES_NETDEV=y
CONFIG_NFT_SOCKET=y
CONFIG_NFT_TPROXY=y
CONFIG_NET_CLS_ACT=y  CONFIG_NET_SCHED=y
```

已通过 `scripts/fuxi-build-abl.sh` 的 `FUXI_NET=1` 开关叠加：

```bash
FUXI_NET=1 FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-audio-disp \
  scripts/fuxi-build-abl.sh net1
```

若用的是旧内核（无 FUXI_NET），TUN 开关会失败并在界面提示；
此时仍可用「系统 HTTP/SOCKS 代理」模式（浏览器/终端走 127.0.0.1:7890）。

## 运行

```bash
# 桌面点图标，或：
su fuxi -c 'env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus clash-fuxi'
```

## 与“原生移植”的关系

无需移植 —— Mihomo 有官方 aarch64 静态二进制，GUI 用系统自带 GTK4/Adw 运行时。
唯一需要“从源码构建”的是内核的网络配置（不改代码，只开 CONFIG 选项）。
