# Xiaomi 13 `fuxi` 主线 Linux 移植

在小米 13（SM8550）上跑**主线内核 + 完整 Ubuntu 用户空间**，做成一台可以日常使用的 Linux 手机。
不使用 Android vendor kernel、Halium 或 Android HAL；Android 与 Linux 双系统共存，普通开机进 Android，
首次部署引导层到 `recovery_b`，以后重启到 recovery 进入 Linux。

当前版本：**v0.6.0**（内核 `7.4.0-rc1`，主线 U-Boot + extlinux 引导，内核以 deb 包升级，Ubuntu 26.10）。

开发基线：`7.4.0-rc1-fuxi-std2` 已上机验收，固定源码、配置和补丁配方见 `kernel/`。
相机实验 `cam4` 已完成超广角 RAW 600 帧、CPU RGB 1200 帧，但全尺寸启流再次阻塞互连与存储；物理恢复后已返回 std2 并通过恢复检查，相机调试按用户要求暂停，见 [`TASKS.md`](TASKS.md)。
标准化收尾记录见 [`验收记录`](docs/标准化收尾验收-20261107.md)，Release 下载仍以各版本说明为准。
构建环境、持续集成、内核更新与签名发布规则见 [`维护与发布`](docs/内核持续维护与发布.md)。
新增维护工具的验收不等同于相机、通话、快充完成；对外发布还需真实作者签字和许可证审核。
本轮构建、故障测试和设备重启结果见 [`维护补强验收`](docs/维护补强验收-20261108.md)。
CPU 短时负载、无线与传感器并行初始化结果见 [`驱动验收`](docs/驱动初始化与功耗验收-20261108.md)；持续功耗和挂起恢复仍待验收。

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
| 相机 | 实验中：OV13B10 超广角 RAW/RGB 已出图；GPU 停帧、全尺寸 CPU 启流互连阻塞、短横线与桌面取景未解决，见[实机记录](docs/相机验收-20261108.md) |
| 指纹 | ❌ 主线不可行（TrustZone 独占） |

充电实测与限制见 [`docs/充电验收-20261107.md`](docs/充电验收-20261107.md)：墙充可净充电；电脑 USB SDP 输入功率较低，使用中可能净放电。**快充尚未解决**：多数测量为 5 V、约 4.4-6.9 W，曾短暂测到 8.825 V / 0.903 A；高电量窗口不能代表峰值能力。chg3 的电池认证和受限限流/协议查询接口已纳入标准源码，但固件限流与 VDM 握手问题仍存在，见 [`快充修复进展`](docs/快充修复进展-20261107.md)与[`开源实现调研`](docs/快充开源实现调研-20261107.md)。

当前维护入口见 [`内核维护与独立升级`](docs/内核维护与独立升级.md)，源码清单与补丁见 [`kernel/`](kernel/README.md)，任务状态见 [`TASKS.md`](TASKS.md)。历次交接保留在 `docs/`，不代替当前操作文档。

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
# 按固定上游压缩包、校验和与分组补丁重建源码
python3 scripts/fuxi-kernel-source.py prepare
# 无网络时：prepare --archive /path/linux-7.4-rc1.tar.gz
scripts/fuxi-build-deb.sh <唯一名字>
```

构建产物在 `work/kbuild/<名字>/`：内核 deb、配置、源码记录、`build.json` 与校验和。
完整云端打包可在 Actions 手动运行 `Full Kernel Package`，保留 deb、对应源码、日志及分阶段计时 7 天；
本地同条件计时入口为 `python3 scripts/fuxi-kernel-benchmark.py <相同名字>`，详见 [`完整包和速度对比`](docs/内核持续维护与发布.md#完整包和速度对比)。
标准源码在 `build/linux-standard/`（不提交第三方源码），默认构建前必须通过完整源码树校验。
安装内核 deb 后由 initramfs-tools 和设备包钩子更新启动项，日常升级不重刷 recovery_b。
构建依赖、实验源码入口和补丁维护见 [`kernel/README.md`](kernel/README.md)。

`build/linux/`、`fuxi-build-abl.sh` 和 `scripts/legacy/` 属于旧 ABL/shim 路径，仅供历史复现。

## 验证与访问

```bash
scripts/fuxi-ssh.sh                       # USB SSH，手机 10.42.0.2；FUXI_HOST=<无线 IP> 走 WiFi
scripts/fuxi-health.sh                    # 只读验收
scripts/fuxi-health.sh --require-wifi --require-sensors
scripts/fuxi-driver-audit.sh 10           # 只读区间采样，JSON 保存在 work/driver-audit/
FUXI_HOST=<手机无线IP> scripts/fuxi-ssh.sh python3 - --chunks 16 < scripts/fuxi-charger-log.py  # 按需读 ADSP 充电日志
scripts/fuxi-test-recovery.sh <镜像> '<检查命令>'   # 写入 recovery_b 并重启验证（会重启手机）
python3 -m unittest discover -s scripts/tests -v
```

`scripts/fuxi-test-recovery.sh` 默认测完回 Android，`FUXI_KEEP=1` 留在 Linux。测试前先核对设备、镜像和备份。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 设计、调研、验收记录与历次交接文档 |
| `scripts/` | 构建、部署、健康检查与回归测试 |
| `kernel/` | 固定源码清单、分组补丁、作者记录与上游整理计划 |
| `device-info/` | 本机器件、分区与内核参数快照 |
| `work/patches/` | 内核与第三方项目的补丁 |
| `work/rootfs-overlay/` | 历史用户空间服务与桌面配置 |
| `apps/` | SuperTerm、Mihomo GUI、DSH 手机前端的权威源码 |
| `packaging/` | 设备包内容、固定第三方源码配方、构建环境与发布门槛 |
| `work/` | 本地构建、日志、实验与设备状态；旧 overlay 只作历史参考 |

## 操作边界

- Linux 侧修改只落在 `linux` 分区和 `recovery_b`；Android 系统与固件分区只读。
- 不写 GPT/槽位、`xbl`/`abl`/`tz`/`hyp`、`modemst1/2`、`fsg`/`fsc`、`persist`、`efs`。
- 备份必须核对哈希后才用于回退。

## 许可证

自有代码没有统一的开源授权，保留已有权利；Linux 和其他第三方代码遵循各自许可证，不能用仓库说明覆盖。
范围和再分发要求见 [`LICENSE.md`](LICENSE.md)、[`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 资料入口

- [`docs/目标-M5.md`](docs/目标-M5.md)：独立可用目标与各外设实验历史
- [`docs/双系统启动说明.md`](docs/双系统启动说明.md)：启动链、切换与恢复
- [`docs/内核维护与独立升级.md`](docs/内核维护与独立升级.md)：当前构建、安装、回退与清理流程
- [`TASKS.md`](TASKS.md)：当前任务与红线
