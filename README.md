# Xiaomi 13 `fuxi` 主线 Linux 移植

在小米 13（SM8550）上跑**主线内核 + 完整 Ubuntu 用户空间**，做成一台可以日常使用的 Linux 手机。
不使用 Android vendor kernel、Halium 或 Android HAL；Android 与 Linux 双系统共存，普通开机进 Android，
需要时写 `recovery_b` 进 Linux。

当前版本：**v0.6.0**（内核 `7.4.0-rc1`，主线 U-Boot + extlinux 引导，内核以 deb 包升级，Ubuntu 26.10）。

## 当前状态

| 能力 | 状态 |
|---|---|
| 启动、UFS 根文件系统、双系统切换 | ✅ 实机稳定（Linux 在 `/dev/sda33`）；`recovery_b` = 主线 U-Boot，内核 deb 独立升级，启动失败 2 次自动回退 |
| 显示、触摸、Phosh 桌面 | ✅ 原生 MSM DSI、120 Hz、Adreno 740 GPU 渲染，用户现场确认 |
| Denial 桌面（mobile shell） | ✅ 0.6.2 官方 arm64 预编译包已安装运行，开机默认（2026-11-07）；`fuxi-session phosh` 回退 |
| WiFi / 蓝牙 | ✅ 5 GHz 自动重连、CN 管制域、蓝牙上电与 L2CAP 链路 |
| 传感器 | ✅ 加速度、光线、距离、磁场、指南针接入 SensorProxy |
| 音频（非通话） | ✅ 双扬声器、主麦克风；⚠️ 保护/标定未做，蓝牙耳机 A2DP 未验收 |
| 电量、充电、调频温控、s2idle | ✅ 可用；⚠️ 续航未达标，空闲自动挂起已关闭 |
| 浏览器 / 应用 | ✅ Chromium（隔离安装）、Epiphany、文件选择器、Clash、DeepSeek Harness |
| 蜂窝数据 | ✅ 双卡注册，上网卡可选卡槽 1/2 |
| 通话、短信、双卡 | ⚠️ 双卡 IMS 注册、短信收发、VoLTE 来电接听实测通过；**通话没有声音**（ADSP TX 图 `start` 返回 -22） |
| 相机 | ❌ 未完成；MCLK 修法已写（`work/patches/camera-mclk-gdsc-20261107/`），未上机 |
| 指纹 | ❌ 主线不可行（TrustZone 独占） |

充电实测与限制见 [`docs/充电验收-20261107.md`](docs/充电验收-20261107.md)：墙充可净充电；电脑 USB SDP 输入功率较低，使用中可能净放电。**快充尚未解决，仍为 5 V 基础输入**；电池认证已在 chg2 实机通过，充电器认证仍在排查，见 [`快充修复进展`](docs/快充修复进展-20261107.md)与[`开源实现调研`](docs/快充开源实现调研-20261107.md)。

细节与逐项证据见 [`docs/`](docs/)，最新交接见 [`docs/交接-新会话-20261107-1652.md`](docs/交接-新会话-20261107-1652.md)，任务摘要见 [`TASKS.md`](TASKS.md)。

## 下载与安装

**仓库 zip 只有源码、文档和脚本，不含任何可刷镜像，也不能直接刷机。**

可刷的东西在 Release 里：

- [v0.6.0](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.6.0-uboot-20261107) —
  `abl-fuxi-uboot7.img`（主线 U-Boot，1.3 MB）、7.4 内核 deb、fuxi-device/desktop/modem 包、完整内核补丁、`.config`、校验和。
  从 v0.5.0 升级要先在 Linux 里装好内核 deb 和 fuxi-device，再换 `recovery_b`，步骤见 Release 说明。
- [v0.5.0](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.5.0-dualboot-20261031) —
  `abl-fuxi-modem16-epmap.img`（10.4 MB）、完整内核补丁、`.config`、源码树快照、校验和。

安装前必须同时满足：

1. 小米 13 已解锁 bootloader；
2. 分区表里有一个 `linux` 分区，且正好是 `/dev/sda33`（**原厂没有这个分区**，作者另行改过 GPT，改错有变砖风险）；
3. `sda33` 里已装好 arm64 Ubuntu rootfs（Release 不提供）。

镜像只写 `recovery_b`，不动 Android 系统分区；起不来时长按电源 20 秒回 Android。
完整前提、步骤和已知问题见 Release 说明与 [`docs/双系统启动说明.md`](docs/双系统启动说明.md)。

> **开发者预览，不是一键安装包。** 主要用途是复现作者的构建与实验结果。

## 从源码构建

```bash
# 日用组合：原生显示 120 Hz + 扬声器/主麦克风 + GPU + 深度空闲
FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp scripts/fuxi-build-abl.sh <名字>

# 无显示驱动的最小镜像（simpledrm）
scripts/fuxi-build-abl.sh daily
```

构建产物在 `work/kbuild/`：`<名字>.img`、`.config`、`.dtb`、`.build.json`。构建脚本自带互斥锁，
成功或失败都会恢复原始 `.config`；变体镜像是实验用，不要只凭编译成功就替换日常镜像。

内核源码在 `build/linux/`（**不在仓库内**，由 `scripts/` 拉取并打补丁）。

## 验证与访问

```bash
scripts/fuxi-ssh.sh                       # USB SSH，手机 10.42.0.2；FUXI_HOST=<无线 IP> 走 WiFi
scripts/fuxi-health.sh                    # 只读验收
scripts/fuxi-health.sh --require-wifi --require-sensors
scripts/fuxi-test-recovery.sh <镜像> '<检查命令>'   # 写入 recovery_b 并重启验证（会重启手机）
python3 -m unittest discover -s scripts/tests -v
```

`scripts/fuxi-test-recovery.sh` 默认测完回 Android，`FUXI_KEEP=1` 留在 Linux。测试前先核对设备、镜像和备份。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 设计、调研、验收记录与历次交接文档 |
| `scripts/` | 构建、部署、健康检查与回归测试 |
| `device-info/` | 本机器件、分区与内核参数快照 |
| `work/patches/` | 内核与第三方项目的补丁 |
| `work/rootfs-overlay/` | 用户空间服务与桌面配置 |
| `work/superterm/`、`work/mihomo-gui/`、`work/dsh-phone/` | 本项目自研的手机应用 |

## 操作边界

- Linux 侧修改只落在 `linux` 分区和 `recovery_b`；Android 系统与固件分区只读。
- 不写 GPT/槽位、`xbl`/`abl`/`tz`/`hyp`、`modemst1/2`、`fsg`/`fsc`、`persist`、`efs`。
- 备份必须核对哈希后才用于回退。

## 许可证

仓库目前**没有附带开源许可证**，默认保留所有权利。如需复用其中代码，请先联系作者。

## 资料入口

- [`docs/目标-M5.md`](docs/目标-M5.md)：独立可用目标与各外设实验历史
- [`docs/双系统启动说明.md`](docs/双系统启动说明.md)：启动链、切换与恢复
- [`docs/交接-新会话-20261103-1940.md`](docs/交接-新会话-20261103-1940.md)：当前最新状态
- [`TASKS.md`](TASKS.md)：当前任务与红线
