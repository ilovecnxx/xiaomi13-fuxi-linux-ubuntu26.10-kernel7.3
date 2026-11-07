# TASKS（2026-11-07 16:40）

**最新交接：`docs/交接-新会话-20261107-1522.md`（设备包收尾 + U-Boot uboot7）**
旧版 TASKS：`docs/TASKS.md.before-20261107-1522`（含设备/通信/桌面包全记录）、`docs/TASKS.md.before-20261107-0734`
通信线：`docs/交接-新会话-20261106-2325.md`；引导层：`docs/计划-标准化启动-20261107.md`；Denial：`docs/Denial桌面安装-20261107.md`
Telegram chat_id 1699534680：所有进度都要转发。

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核以 deb 独立升级、标准引导、设备支持全部打包。

## 当前状态
- 引导：ABL → recovery_b = U-Boot uboot7（b78e2462…）→ extlinux → deb5 + initramfs-tools initrd；2 次未确认自动回 deb4。
- 内核 deb5（语音工作树 `~/.cache/fuxi-voice74` 构建）= 收敛树 a5dcbb9cc（花屏修复）+ 语音移植（未提交，默认关）。
- cmdline loglevel=3（11-07 16:31 起；旧的备份 /etc/kernel/cmdline.bak-20261107-loglevel7）。
- 手动兜底条目：`fuxi-bootcount manual-once` 后下次启动走 env 固定 booti 条目（不要按音量下，按不中）。
- cmdline 只用 root=UUID（PARTUUID 会让 initramfs 等 30 s）。
- 包（本地源 /srv/fuxi-apt）：fuxi-device / fuxi-desktop 0.20261107.1519；fuxi-modem 0.20261107.1619（卡 2 按 PLMN 选 APN + fuxi-mobile-auto）。
- 81voltd v8（WDS 重试固定 20 s，补丁 work/patches/81voltd-sub2-20261101/0002-wds-retry-20s.diff）：卡 2 IMS 127 s 注册（旧 185–210 s）。
- 构建：`scripts/fuxi-build-pkgs.sh` → rsync work/apt/ → 手机 apt install；packaging/ 是唯一源（scripts/ 重复副本已删）。

## 下一步（用户已同意 A–E，A 完成；刷机/重启/进 Android 仍要先问）
- B1 挂起：未测（要用户在旁：rtcwake -m mem -s 30、电源键唤醒、来电/短信唤醒，再查屏/USB/WiFi/modem/IMS）。
- B2 通话音频：deb5 上 start(TX) 仍 -22（GRAPH_START 0x01001002 DSP error 1；RX 全成功；参数 experimental=1 extra=57 replay=0，`amixer -c0 cset name='Voice Call' SIM2`）。
  下一个【推测】：ADSP 固件是原厂 309、抓包来自 307 → 换整套 307 adsp 测（先备份 309），待用户同意。
- B3 GPU：硬件加速已确认，Vulkan（turnip）已装。
- C：卡 2 IMS 已改善；移动卡 APN 已写（未上机测，没有移动卡）；5G 自动回切未做（要用户同意约 3 s 断网）。
- D 花屏：【已实测】SMMU fault 10→1（deb4）→0（deb5），补丁 work/patches/display-splash-iommu-20261107/。用户反馈 deb4 花屏变短，deb5 视觉待确认。
- E：adsp 来源已查清（24 个 = 原厂 309 NON-HLOS，fuxi-firmware-pull.sh 会标 stock309）；shim 退役、defconfig、上游补丁未做。

## 用户最新指令（11-07 16:40）
- 先做 E（shim 退役 / defconfig 整理 / 上游补丁分组 + checkpatch），再查蓝牙、WiFi 省电和功耗管理 bug（WCN7850：ath12k power_save、PCIe ASPM、hci_qca 关 BT 时是否仍上电、电池电流、唤醒源、cpuidle 深睡）。
- 16:45 子 agent 因 API 余额不足（403 insufficient balance）没跑起来，两项都还没开始；先只读调研，变更前在 Telegram 问。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- recovery_b 只用 `scripts/fuxi-test-recovery.sh`；回滚 uboot5 → uboot4 → shim7。试验间不刷回基线。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作；删大文件。
- 重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`；严禁远程重启 phosh/denial。
- 音频镜像 `FUXI_DTB=…-modem-audio-disp`；不提交 q6voice-*.h、android-voice-*、voice-shmdump*、MM 源码、固件、work/apt、work/modem-bin。
- 手机号不进文档/提交；GitHub 公开仓库推前扫描；dmesg / >20 KB 文件交子 agent。
