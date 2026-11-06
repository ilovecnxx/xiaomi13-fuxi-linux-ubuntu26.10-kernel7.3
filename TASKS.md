# TASKS（2026-11-06 12:27）

**最新交接：`docs/交接-新会话-20261106-1227.md`（USB 线）**
旧交接：`docs/交接-新会话-20261103-1940.md`（语音线）；桌面验收：`docs/验收-20261104-上机.md`；旧版 TASKS：`docs/TASKS.md.before-20261105-2243`

## 目标
小米 13（fuxi）主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 音频）→ 双卡；桌面保持。

## 当前状态
- 设备：在 Ubuntu，内核 #273，recovery_b 里是 `abl-fuxi-usbc-dp1.img`（SHA `9ff110da`），**不是基线**。
  基线 `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`，#222）。现场的 UCSI unbind、dwc3 强制 host 是临时状态，重启后恢复。
  `fuxi-serial-shell` 已 `disable`（永久，重启不恢复），改为需要时手动 `/usr/local/sbin/fuxi-usb-serial on`。
- USB 线：
  - 实测通过：UCSI；dwc3 切 host 后 xHCI 起 USB2 bus1 和 USB3 bus2（前提是先放开 ttyGS0）。
  - 失败：OTG U 盘不识别（推测 VBUS 未供出，或 eUSB2 repeater 缺 host 调参）。
  - 未测：DP 显示（只确认驱动加载，没有显示器）；被电脑识别、休眠后识别。
  - DP 变体的 DTS、Makefile、构建脚本没提交。
- 充电线（暂停）：PD 快充仍是 5 V，详见旧版 TASKS 和 `work/patches/battmgr-units-20261105/`。
- UFS G5（搁置）：负载中 `reboot -f` 黑屏卡死；补丁在 `work/patches/pending/`，没提交。
- 蓝牙 120 s 超时、SSC 400 缺失：已在 11-06 修好（serdev 重绑，`fuxi-firmware-devices`）。
- 语音线（暂停）：start(RX) 成功；start(TX) 仍 -22，0x43ad 方案已否定。
- 桌面线：Plasma Mobile 是默认会话；待做 Plasma 下日用验收、90 Hz、A2DP。

## 下一步（USB 线，细节见交接 §9）
1. 只读确认现场（uname、boot_id、dwc3 mode）。
2. 子 agent 反汇编原厂 `qti_battery_charger.ko`，找 VBUS_DISABLE、OTG_UI_SUPPORT、CC_TOGGLE 的真实下标和 opcode。
3. 请用户用电表量 OTG 口有没有 5 V，再决定改 VBUS 还是改 repeater 调参。
4. ~~ttyGS0 永久修复~~ ✅ 11-06：`fuxi-serial-shell` 改手动启动（新增 `work/rootfs-overlay/usr/local/sbin/fuxi-usb-serial`，手机已 disable）。实测 device→host 切换不再阻塞，xHCI bus1/bus2 正常起。
5. 请用户决定基线：采用 #273，还是回滚到 20ac7794。提交 DP 变体前问用户。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
- 无线 adb 刷机必须设 `ANDROID_SERIAL=<WiFi 端点>`。
