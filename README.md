# Xiaomi 13 `fuxi` 主线 Linux 移植

在小米 13（SM8550）运行主线内核和完整 Ubuntu 用户空间，目标是一台日常可用的 Linux 手机。
不使用 Android vendor kernel、Halium 或 Android HAL；保留 Android 双系统。
引导链为 U-Boot、extlinux、initramfs-tools，日常内核通过 deb 升级。
最新公开版本是无签名的开发者预览 `v0.9.0-rc1`，使用 Linux 7.4.0-rc3、Ubuntu 26.10；
它不代表稳定版，也不代表手机已完成完整硬件验收。

## 开发入口

整个项目采用快速迭代：实现一个可观察结果，做必要检查，运行验证，再按结果继续。
沿用最新通过相应验证的成果，不例行回退，不为每个小改动新增报告或配方层。

| 入口 | 职责 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 通用开发、测试、提交和操作边界 |
| [TASKS.md](TASKS.md) | 唯一当前状态：优先级、暂停、源码/设备基准、最近结果和下一步 |
| [内核操作](docs/内核维护与独立升级.md) | 构建、安装、正常重启、恢复与清理 |
| [内核配方](kernel/README.md)、[设备包](packaging/README.md)、[应用](apps/README.md) | 各模块源码归属与构建入口 |
| [维护与发布](docs/内核持续维护与发布.md) | 集成和公开发布，不是每轮实验的前置门槛 |
| [本次精简记录](docs/项目精简记录-20261116.md) | 已合并/删除内容、历史找回方式与保留边界 |

历史报告和交接只保留证据，不作为当前操作指令。设备版本及实验状态只在 `TASKS.md` 更新。

## 能力概览

| 能力 | 已知范围 |
| --- | --- |
| 启动与升级 | UFS 根文件系统、内核 deb、多内核启动及自动回退已有实机验证 |
| 显示与桌面 | 显示、触摸、Adreno GPU、Phosh 可用 |
| 网络与蜂窝 | WiFi、蓝牙基础链路、双卡注册及数据可用；A2DP 仍待验收 |
| 传感器与定位 | 多种传感器有读数；真实轴向、桌面集成、GNSS 有效 fix 等未完成 |
| 音频与通话 | 扬声器、麦克风可用；通话无声，保护/标定未完成 |
| 电源 | 电量、充电、温控可用；持续快充、挂起和续航未完成 |
| 应用 | 浏览器、文件管理及项目自有应用可用；具体缺口见当前队列 |
| 相机 | 主摄基础流程未完成；验收范围见[基础相机目标](docs/基础相机目标.md) |
| NFC、指纹 | 未接通 |

这是部分功能可用的移植，不等于原厂完整能力。详细功能边界见
[覆盖核查](docs/功能覆盖核查-20261108.md)和[相机矩阵](docs/相机功能完成矩阵.md)。

## 下载与安装

**仓库 zip 是源码和工具，不是可刷镜像；Release 是开发者预览，不是一键安装包。**

- [v0.9.0-rc1 Release](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.9.0-rc1)：无签名 Linux 7.4-rc3 ARM64 内核 deb、匹配源码归档和校验和；不包含固件、校准数据、兼容 DTB 或引导镜像。
- [v0.6.0 Release](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.6.0-uboot-20261107)：历史 U-Boot、内核 deb、设备包、补丁和校验和。
- [v0.5.0 历史 Release](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.5.0-dualboot-20261031)：旧引导路径，仅供历史复现。

首次安装需要已解锁 bootloader，已经正确创建且位于 `/dev/sda33` 的 `linux` 分区，
以及其中的 arm64 Ubuntu rootfs。原厂没有此分区，Release 不提供 rootfs。
本项目开发流程不授权修改 GPT。首次引导部署与已有 Linux 的日常升级是两种操作，
完整前提及恢复说明见[双系统启动说明](docs/双系统启动说明.md)和对应 Release。
首次部署引导层涉及 `recovery_b`；日常内核升级仅写 Linux 分区，不重刷引导层。
BCB 残留 `boot-recovery` 时普通重启不保证返回 Android。

## 构建与检查

```bash
python3 scripts/fuxi-kernel-source.py prepare
# 离线：prepare --archive /path/linux-7.4-rc3.tar.gz
scripts/fuxi-build-deb.sh <唯一名字>
bash scripts/fuxi-ci.sh docs       # 活跃文档本地链接
bash scripts/fuxi-ci.sh scripts    # 脚本回归，不下载内核
bash scripts/fuxi-ci.sh apps       # SuperTerm 回归
```

内核产物在 `work/kbuild/<名字>/`，包含 deb、配置、准确源码记录和校验和。
本地迭代复用兼容的输出目录；固定环境、实验源码入口及恢复方法见上面的模块文档。
云端 `Full Kernel Package` 保留独立干净构建，用于完整包与性能对比，不替代本地增量开发。

访问与健康检查使用 `scripts/fuxi-ssh.sh`、`scripts/fuxi-health.sh`；
当前版本与暂停状态先查 `TASKS.md`。详细日志和产物留在 `work/`，不反复复制到入口文档。
`build/linux/`、旧 overlay 和 `scripts/legacy/` 仅供历史复现。

## 许可证

自有代码没有统一开源授权，保留已有权利；第三方代码遵守各自许可证。
再分发要求见 [LICENSE.md](LICENSE.md)、[THIRD_PARTY.md](THIRD_PARTY.md)。
私钥、专有固件、生成镜像及原始设备日志不入库；公开发布与本地实验分开。
