# TASKS（2026-11-02 05:45）

**完整交接：`docs/交接-新会话-20261102-0540.md`**（上一版 `docs/交接-新会话-20261102-0237.md`；旧 TASKS 备份 `docs/TASKS.md.before-handoff-20261102-0540`）

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态
- 基线 #222（recovery_b 前缀 20ac7794）；boot_id 35f7efc4（11-02 06:20 刷回）；两卡 IMS registered；fuxi-health 失败 0。
- 81voltd-sub2 = v5（77460da0）；ModemManager = mm-netlink UAF 修复版（863c42bd）。
- 【已实测，#236，设计文档 §7o】模式 0 里给 TX 补 SG/容器额外属性（`voice_extra` bit0）后 VSID 通过；只补 2 条图内连线（`voice_extra`=2）仍被拒、modem 崩溃。
- 【已实测，#237，§7p】再加 TX 端点 SET_CFG（bit3，0x08001017 + 0x08001063，缺的两个字是猜的）：两条都返回 0，`start(TX)` 仍回 status 1。
- 卡点：`start(TX)`（GRAPH_START）status 1。【推测】缺 0x01001008 校准块或 VCPM 的 0x080011bc/0x08001310/0x0800116b；抓包只存了每包前 40 字节，要全长得重新抓（进 Android，要问用户）。
- 主仓库 HEAD 见 `git log`（已提交）；内核树 `7f3051fe4` 之后的 `audioreach.c/.h`（`audioreach_apm_set_param`）、`q6apm-voice.c`（`voice_extra` bit3）、`q6apm.c/.h` 和 `q6voice-{ctrl,oob,agraph}.h` 未提交。试验镜像 `work/kbuild/abl-fuxi-voice-extra2.img`（前缀 2174a2cb）。

## 下一步
1. 问用户：是否进 Android 重新抓包并存全长载荷（`fuxi-gpr-shmdump`，要附着 HAL、打电话有费用）；或先离线读 0x01001008 的 shm 块（pkt0141/0147 的 map 文件）看能否推出校准块内容。
2. 方案定了再问用户批准上机；失败的先跑，通过的最后跑。试验后回基线 #222 并跑 fuxi-health。
3. 问用户：上游 #1110 留言（用户自己贴，N 要核对）；内核树剩余文件怎么提交。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线 #222 并跑 fuxi-health。
- 不要单独重启 81voltd；不要带参数运行 81voltd；不要用 `--set-primary-sim-slot`；顺序重启是 stop 81voltd、restart MM、start 81voltd。
- 刷机/重启、替换二进制、restart MM、kill qmi-proxy、建 PDN 试验、ADSP 命令、进 Android（含附着 HAL）、打电话、费用操作、git commit/push、对外留言都要先问用户。
- 不要重试：往图里补外部连线；原样重放 5 个块；补 0x08001162/0x08001061 求 VSID；图端口作 VCPM 源端口；kctl 原样再写 SIM2；卡 2 卡 ctnet 时顺序重启或换 MM；连续 kill qmi-proxy；改 Start Network 参数；按「默认校准块」写 0x01001008。
- 一次开机里不要先跑 Android 块模式再跑 mode 0（modem 崩溃，§7k）。不用 `pkill -f`。adb 用 `tools/platform-tools/adb -s f8286c82`。不提交 `work/claude-logs/android-voice-*`、`q6voice-oob.h`、`q6voice-agraph.h`。报告区分实测与推测。
