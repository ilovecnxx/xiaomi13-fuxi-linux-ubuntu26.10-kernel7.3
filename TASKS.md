# TASKS（2026-11-04 01:05）

**完整交接：`docs/交接-新会话-20261103-1940.md`**

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态（2026-11-04 01:05）
- 【✅ 重大突破】**start(RX) 成功**：删除 RX prepare + STATE(1) + extra=57，多次测试稳定重现
- 【❌ 待解决】start(TX) -22（DSP Error 1，cmd 0x01001002）
- 【❌ 已否定·11-04 实测】TX 端点 miid 0x43af→0x43ad（7 处全改）+ TX EP 参数对齐 Android #934/#936（1 声道、掩码 0x8）：
  两条 TX EP SET_CFG ret=0，但 start(TX) 仍 -22。镜像 `abl-fuxi-voice-tx-43ad.img`（SHA `55f2471f`），
  日志 `work/claude-logs/rtest-20261104-005609/ssh.txt`。改动在 `build/linux` 工作树，未提交。
- 【已恢复】recovery_b = `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`），设备在 Android
- 内核 HEAD：`e93bbc190`（+ 未提交的 0x43ad/1 声道改动）；harness HEAD：`3188fc3`

## 下一步
1. 【进行中】`voice-tx-43ad` + `voice_replay=1`：Android 在 TX OPEN 后、START 前发 OOB SET_CFG（#928）和
   `0x01001008` 校准块（#932）；RX 修复（11-03 19:37）后还没开过 replay。
2. 若仍失败：从 Android VoLTE TX 图（#928 OOB）提取结构，重新生成 TX 图定义。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。
- ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
