# TASKS（2026-11-06 23:25，项目暂停）

**最新交接：`docs/交接-新会话-20261106-2325.md`（双卡/VoLTE/keeper）**
完整旧版 TASKS（通信线全部实测记录、USB 线下一步）：`docs/TASKS.md.before-20261106-2325`
更早的交接：`docs/交接-新会话-20261106-1340.md`（USB 线）、`…20261103-1940.md`（语音线）

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机：数据 → 短信 → 通话（VoLTE + 音频）→ 双卡；桌面保持。

## 当前状态
- 内核 #278（面板冷启动 + initramfs 3 s 窗口），recovery_b = `abl-fuxi-abl-fuxi-autoboot3.img`（d483a191…）；基线 20ac7794。
- 卡槽 1 = 电信，卡槽 2 = 联通。数据、短信、VoLTE 信令、来电两卡都通过；**通话音频不通（语音 TX -22）**。
- 双卡设置 GUI（`fuxi-sim-settings` → pkexec `fuxi-simctl`）已部署；上网卡 1，**拨号卡 2**。
- 已知限制：卡 2 上网时卡 1（电信）没有 VoLTE（modem 限制）。
- 23:25 卡 2 IMS 在 81voltd 重启 5 min 后仍未注册 → 新会话先查（交接 §7.1）。
- USB 线、充电线、UFS G5、语音线：暂停，见旧版 TASKS。

## 引导层 + 独立升级内核（2026-11-07）
- recovery_b = `work/kbuild/abl-fuxi-shim7.img`（b78d3f73…）：shim 内核 + `scripts/fuxi-shim/init`，从 sda33 `/boot/fuxi/` 选条目 kexec。
  回退：2 次未确认 → fallback；默认条目加载失败 → 立即改用 fallback；都不行 → 应急模式（shim 内核直接进 Ubuntu）。
- 内核以 deb 安装：`FUXI_DEB=1 FUXI_DTB=…-usbc-dp FUXI_EXTRA_CFG="-d FUXI_FORCE_BUILTIN_DTB" [FUXI_KTREE=<树>] scripts/fuxi-build-abl.sh <名字>`
  → 手机 `dpkg -i` → `/etc/kernel/postinst.d/zz-fuxi` → `fuxi-kernel-install`（`scripts/fuxi-boot/`）。
- 手机上：默认 `7.4.0-rc1-fuxi-deb2`（实测基带 5G、显示、电池、音频、触摸、WiFi），回退 `7.3.0-fuxi-deb1`，另有 t281。
- 内核分支：`fuxi/daily`（7.3）、`fuxi/daily-7.4`（worktree `~/.cache/fuxi-74/wt`，基于 tag `fuxi/base-v7.4-rc1`）、`fuxi/voice-exp`（daily + 语音实验）。
- shim DTS 约束见 `sm8550-xiaomi-fuxi-shim.dts` 头注释（nolpi、无 ADSP、无基带 PAS、rmtfs 只删 compatible、不碰触摸 QUP0/GPI）。
- 测试刷机：手机进 Android 后 `FUXI_KEEP=1 FUXI_SSH_WAIT=240 scripts/fuxi-test-recovery.sh <镜像>`；本机 fastboot 枚举不稳。
- 待做：initramfs 从内核拆成条目 initrd；`~/.cache/fuxi-split`（2.3 GB）可清。
- **11-07 深夜：recovery_b = 主线 U-Boot `work/kbuild/abl-fuxi-uboot3.img`（57da97c5…）**，取代 shim：
  ABL → U-Boot（`scripts/fuxi-build-uboot.sh`，env `scripts/fuxi-uboot/fuxi.env`）→ 自动 sysboot `/boot/extlinux/extlinux.conf`
  （fuxi-kernel-install 生成）→ 内核。按住音量下 = U-Boot 菜单；extlinux 菜单 3 s 可选回退条目。
  **现 recovery_b = uboot4（b30bfcb3…）**：带自动回退（`fuxi-bootcount` 计数块，2 次未确认 → fallback.conf），三种情况实测通过。
  内核树收敛：分支 `fuxi/consolidate-7.4`（单文件 `sm8550-xiaomi-fuxi.dts`，dtc 只差 bootargs；`fuxi_defconfig` 与 deb2 零差异）。
  **①完成（11-07）：默认 `7.4.0-rc1-fuxi-deb3`** = `scripts/fuxi-build-deb.sh deb3`（fuxi_defconfig，无内建 initramfs/cmdline，
  DTB `qcom/sm8550-xiaomi-fuxi.dtb`）+ initramfs-tools initrd + `/etc/kernel/cmdline`（root=UUID=，不能用 PARTUUID=：wait-for-root 等 30 s）。
  以后升级内核：`scripts/fuxi-build-deb.sh <名字>` → 手机 `dpkg -i` 即可（自动生成 initrd、条目、extlinux、arm 计数）。回退 deb2。
  回滚 shim：`scripts/fuxi-test-recovery.sh work/kbuild/abl-fuxi-shim7.img`。详情 `docs/计划-标准化启动-20261107.md` ③。

## Denial 桌面（2026-11-07）
- 【已实测】手机装官方预编译 `denial 0.6.2-1` + `denial-flutter-engine 0.6.2-1`（arm64 签名包，
  按 SHA256SUMS 校验），mobile shell 正常运行：DSI-1 1080×2400@120 Hz、scale 1.75，
  应用网格/设置/终端可交互；`deniald` 约 17 % CPU，37–42 °C。
- 2026-10-31 的“手机上从源码编 Flutter engine”方案放弃（ninja 648/4525 被 OOM 杀掉）；
  手机 `/home/fuxi/denial-cache`（15 GB）+ `denial-src` 已于 11-07 删除（根分区 30→16 GB）。
- 2026-11-07 04:03 起默认图形会话改为 Denial（disable phosh / enable denial，回退相反）。切换：`fuxi-session denial` / `fuxi-session phosh`（实测 17 s；
  phoc 偶尔对 SIGTERM 不退出，systemd 90 s 超时后 SIGKILL）。unit `denial.service`、
  切换脚本在 `work/rootfs-overlay/`；安装脚本 `work/denial-arm64/install-prebuilt.sh`。
- 【已修】Denial 下 DeepSeek App / Chromium 浏览器打不开：unit 写死 `WAYLAND_DISPLAY=wayland-0`，
  Denial 的 socket 是 `wayland-1`。改成运行时探测（`work/dsh-phone/install-remote.sh`、
  `work/chromium-mobile/fuxi-mobile-shell-run`），实测两应用都能起。
- 冷启动路径【实测通过】：11-07 04:15 / 04:24 两次重启都直接进 Denial。
- 待办：退出 Denial 后自动回 Phosh；`scripts/fuxi-basic-check.py` 适配 Denial 会话；
  双屏/旋转/休眠/输入法验收。
  详情 `docs/Denial桌面安装-20261107.md`。

## 下一步
1. 只读查卡 2 IMS（交接 §9.1–9.2）。
2. 5G 按信号自动回切（只有设计，交接 §9.3；会断网约 3 s，先问用户）。
3. 移动卡：按 PLMN 自动选 APN（交接 §7.5、§9.4）。
4. GUI：上网卡 2 + 拨号卡 1 的警告。
5. 提交前问用户的 USB 线遗留文件（旧版 TASKS"下一步"第 5 条）。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验间不刷回基线，直接刷修正版（用户 11-07）。
- 音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`；ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`、MM 源码。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
- 严禁远程重启 phosh.service；重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`。
- 无线 adb 刷机必须设 `ANDROID_SERIAL=<WiFi 端点>`。用户手机号不写进文档或提交。
