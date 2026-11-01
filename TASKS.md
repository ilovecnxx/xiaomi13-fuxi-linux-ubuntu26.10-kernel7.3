# TASKS（2026-11-02 05:45）

**完整交接：`docs/交接-新会话-20261102-0540.md`**（上一版 `docs/交接-新会话-20261102-0237.md`；旧 TASKS 备份 `docs/TASKS.md.before-handoff-20261102-0540`）

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态
- 基线 #222（recovery_b 前缀 20ac7794）；boot_id 50ac895b（11-02 05:53 刷回）；两卡 IMS registered；fuxi-health 失败 0。
- 81voltd-sub2 = v5（77460da0）；ModemManager = mm-netlink UAF 修复版（863c42bd）。
- 【已实测，#236，设计文档 §7o】模式 0 里给 TX 补 SG/容器额外属性（`voice_extra` bit0）后 VSID 通过；只补 2 条图内连线（`voice_extra`=2）仍被拒、modem 崩溃。bit0 的作用与 mask 2 的失败各只观测一次。
- 现在卡点与 Android 块模式一样：`start(TX)`（GRAPH_START）回 status 1。【推测】缺图外的东西：端点配置（0x2012 SET_CFG、tx_ep）、校准块 0x01001008。
- 主仓库 HEAD 79f6e48；内核树 HEAD 1915bcc92（`audioreach.c/.h` 新增可选 SG/容器额外属性，`q6apm-voice.c` 新增 `voice_extra`）。全部未提交；试验镜像 `work/kbuild/abl-fuxi-voice-extra.img`（前缀 ffbd37d2）。

## 下一步
1. 离线研究 `start(TX)` status 1：对比 Android 抓包里 START 之前发的命令（0x01001008 在 pkt0111/0114/0117/0141/0147，0x2012 SET_CFG、端点配置），设计方案写入设计文档 §7p。
2. 方案定了再问用户批准上机；失败的先跑，通过的最后跑。试验后回基线 #222 并跑 fuxi-health。
3. 问用户：git commit（依赖不提交的头文件）；上游 #1110 留言。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线 #222 并跑 fuxi-health。
- 不要单独重启 81voltd；不要带参数运行 81voltd；不要用 `--set-primary-sim-slot`；顺序重启是 stop 81voltd、restart MM、start 81voltd。
- 刷机/重启、替换二进制、restart MM、kill qmi-proxy、建 PDN 试验、ADSP 命令、进 Android（含附着 HAL）、打电话、费用操作、git commit/push、对外留言都要先问用户。
- 不要重试：往图里补外部连线；原样重放 5 个块；补 0x08001162/0x08001061 求 VSID；图端口作 VCPM 源端口；kctl 原样再写 SIM2；卡 2 卡 ctnet 时顺序重启或换 MM；连续 kill qmi-proxy；改 Start Network 参数；按「默认校准块」写 0x01001008。
- 一次开机里不要先跑 Android 块模式再跑 mode 0（modem 崩溃，§7k）。不用 `pkill -f`。adb 用 `tools/platform-tools/adb -s f8286c82`。不提交 `work/claude-logs/android-voice-*`、`q6voice-oob.h`、`q6voice-agraph.h`。报告区分实测与推测。
