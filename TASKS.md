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

## 下一步
1. 只读查卡 2 IMS（交接 §9.1–9.2）。
2. 5G 按信号自动回切（只有设计，交接 §9.3；会断网约 3 s，先问用户）。
3. 移动卡：按 PLMN 自动选 APN（交接 §7.5、§9.4）。
4. GUI：上网卡 2 + 拨号卡 1 的警告。
5. 提交前问用户的 USB 线遗留文件（旧版 TASKS"下一步"第 5 条）。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- 音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`；ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`、MM 源码。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
- 严禁远程重启 phosh.service；重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`。
- 无线 adb 刷机必须设 `ANDROID_SERIAL=<WiFi 端点>`。用户手机号不写进文档或提交。
