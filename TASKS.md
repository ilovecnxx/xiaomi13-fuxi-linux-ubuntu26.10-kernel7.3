# TASKS（2026-11-07 16:52）

**最新交接：`docs/交接-新会话-20261107-1652.md`（B/C/D/E 推进 + deb5；下一步相机）**
上一份：`docs/交接-新会话-20261107-1522.md`；E 调研：`docs/调研-E收尾-20261107.md`；相机：`docs/相机调研.md`
通信线：`docs/交接-新会话-20261106-2325.md`；引导层：`docs/计划-标准化启动-20261107.md`。Telegram chat_id 1699534680：所有进度都要转发。

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核以 deb 独立升级、标准引导、设备支持全部打包。

## 当前状态
- 引导：ABL → recovery_b = uboot7（b78e2462…）→ extlinux → deb5（default）/ deb4（fallback），2 次未确认自动回退。
- deb5 = 收敛树 a5dcbb9cc（花屏修复）+ 语音移植（未提交，默认关），由 `~/.cache/fuxi-voice74` 构建。cmdline loglevel=3。
- 开机花屏 SMMU fault 10→0（已实测）；卡 2 IMS 127 s（81voltd v8）；双卡 IMS 正常。
- 包：fuxi-device/desktop/firmware 0.20261107.1519，fuxi-modem 0.20261107.1619。
- 未提交：fuxi-kernel-install 的 shim7 initrd 硬链接（手机已部署）、E 调研、本交接（用户已同意提交）。
- 子 agent 16:45 起 API 余额不足（403），充值前别派。

## 下一步
1. 提交上述三项（扫描后 push）。
2. 相机：MCLK（camcc mclk 分支 stuck off）→ OV13B10 超广角（主线有驱动）→ libcamera soft ISP。新内核 deb6。
3. E 剩余（defconfig 精简、上游补丁、清 shim1–6/t281）→ 蓝牙/WiFi 功耗 → 挂起测试（用户在旁）→ 语音换 307 adsp（待用户同意）。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- recovery_b 只用 `scripts/fuxi-test-recovery.sh`；回滚 uboot5 → uboot4 → shim7。试验间不刷回基线。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作；删大文件。
- 重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`；严禁远程重启 phosh/denial。
- 不提交 q6voice-*.h、android-voice-*、voice-shmdump*、MM 源码、固件、work/apt、work/modem-bin、work/patches/pending/*。
- 手机号不进文档/提交；GitHub 公开仓库推前扫描；dmesg / >20 KB 文件交子 agent。
