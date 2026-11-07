# TASKS（2026-11-07 15:22）

**最新交接：`docs/交接-新会话-20261107-1522.md`（设备包收尾 + U-Boot uboot7）**
旧版 TASKS：`docs/TASKS.md.before-20261107-1522`（含设备/通信/桌面包全记录）、`docs/TASKS.md.before-20261107-0734`
通信线：`docs/交接-新会话-20261106-2325.md`；引导层：`docs/计划-标准化启动-20261107.md`；Denial：`docs/Denial桌面安装-20261107.md`
Telegram chat_id 1699534680：所有进度都要转发。

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核以 deb 独立升级、标准引导、设备支持全部打包。

## 当前状态
- 引导：ABL → recovery_b = U-Boot uboot7（b78e2462…）→ extlinux → deb3 + initramfs-tools initrd；2 次未确认自动回 deb2。
- 手动兜底条目：`fuxi-bootcount manual-once` 后下次启动走 env 固定 booti 条目（不要按音量下，按不中）。
- cmdline 只用 root=UUID（PARTUUID 会让 initramfs 等 30 s）。
- 包（本地源 /srv/fuxi-apt）：fuxi-device / fuxi-modem / fuxi-desktop / fuxi-firmware 0.20261107.1519，全部重启实测。
- 构建：`scripts/fuxi-build-pkgs.sh` → rsync work/apt/ → 手机 apt install；packaging/ 是唯一源（scripts/ 重复副本已删）。
- HEAD 6e5ac6f 已推送，工作树干净。

## 下一步（用户已同意 A–E，A 完成；刷机/重启/进 Android 仍要先问）
- B 功能缺口：挂起默认开 → 通话音频（语音 TX -22）→ GPU 加速确认。
- C 通信：卡 2 IMS、5G 自动回切、移动卡 APN。
- D 开机花屏：DT splash reserved-memory 加 iommu-addresses（内核 deb4）。
- E 标准化：shim 退役、adsp 来源 local 查清、defconfig/模块、上游补丁。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- recovery_b 只用 `scripts/fuxi-test-recovery.sh`；回滚 uboot5 → uboot4 → shim7。试验间不刷回基线。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作；删大文件。
- 重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`；严禁远程重启 phosh/denial。
- 音频镜像 `FUXI_DTB=…-modem-audio-disp`；不提交 q6voice-*.h、android-voice-*、voice-shmdump*、MM 源码、固件、work/apt、work/modem-bin。
- 手机号不进文档/提交；GitHub 公开仓库推前扫描；dmesg / >20 KB 文件交子 agent。
