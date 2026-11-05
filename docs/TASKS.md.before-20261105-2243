# TASKS（2026-11-04 03:20）

**完整交接：`docs/交接-新会话-20261103-1940.md`；桌面侧验收：`docs/验收-20261104-上机.md`**

## 桌面线（当前主线，通信暂停）
- 设备现在跑在 Linux（Plasma Mobile），不是 Android。
- 已装 KDE Plasma Mobile 6.7.5，`plasmamobile.service` 为开机默认会话，中文界面；`fuxi-session plasma|phosh` 切换。
- 已修：Chromium 重启退出 21（清理残留 SingletonLock）、自动旋转方向（mount matrix）、
  Plasma 自动旋转（kwin 未链 QtSensors → `fuxi-autorotate` 调 `kscreen-doctor`，提交 32535d2）。
- 待做：在 Plasma 会话下重跑日用验收（熄屏唤醒、软键盘、Chromium、文件选择器、亮度）；
  90 Hz / 动态刷新（`kscreen-doctor -o` 显示 DSI-1 有 120/90/60 三档）；拔线开机、蓝牙 A2DP。

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态（2026-11-04 01:15）
- 【✅ 重大突破】**start(RX) 成功**：删除 RX prepare + STATE(1) + extra=57，多次测试稳定重现
- 【❌ 待解决】start(TX) -22（DSP Error 1，cmd 0x01001002）
- 【❌ 已否定·11-04 实测】TX 端点 miid 0x43af→0x43ad（7 处全改）+ TX EP 参数对齐 Android #934/#936（1 声道、掩码 0x8）：
  两条 TX EP SET_CFG ret=0，但 start(TX) 仍 -22。镜像 `abl-fuxi-voice-tx-43ad.img`（SHA `55f2471f`），
  日志 `work/claude-logs/rtest-20261104-005609/ssh.txt`。改动在 `build/linux` 工作树，未提交。
- 【已恢复】recovery_b = `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`），设备在 Android
- 内核 HEAD：`e93bbc190`（+ 未提交的 0x43ad/1 声道改动）；harness HEAD：`3188fc3`

## 下一步
1. 【⚠️ 无结论·11-04 01:07】`voice-tx-43ad` + `voice_replay=1` + extra=57：`amixer cset` 卡住，ssh 120 s 超时，
   dmesg 未抓到（`work/claude-logs/rtest-20261104-010649/ssh.txt`）；systemctl reboot 正常回 Android。
   重测需把 `amixer` 放后台、另起 ssh 抓 dmesg（Android 在 TX OPEN 后、START 前发 OOB SET_CFG #928 和
   `0x01001008` 校准块 #932）。
2. 若 replay 仍失败：从 Android VoLTE TX 图（#928 OOB）提取结构，重新生成 TX 图定义。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。
- ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
