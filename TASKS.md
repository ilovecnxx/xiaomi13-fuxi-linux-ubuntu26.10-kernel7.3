# TASKS（2026-11-03 19:40）

**完整交接：`docs/交接-新会话-20261103-1940.md`**

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态（2026-11-03 19:40）
- 【✅ 重大突破】**start(RX) 成功**：删除 RX prepare + STATE(1) + extra=57，多次测试稳定重现
- 【❌ 待解决】start(TX) -22：TX 图结构错误（用 0x43af 早期图，需 0x43ad VoLTE 图）
- 【已恢复】recovery_b = `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`），设备在 Android
- 内核 HEAD：`e93bbc190`；harness HEAD：`3bf72377`

## 下一步（TX 图重建）
1. 快速验证：修改 q6voice-graphs.h line 48，将 TX EP miid 从 0x43af 改为 0x43ad
2. 或完整重建：从 Android VoLTE TX 图（#928 OOB）提取结构，重新生成 TX 图定义

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。
- ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
