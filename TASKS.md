# TASKS（2026-11-01 22:53）

**完整交接：`docs/交接-新会话-20261101-2253.md`**（上一版 `docs/交接-新会话-20261101-2222.md`；旧 TASKS 备份 `docs/TASKS.md.before-handoff-20261101-2253`）

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态
- 基线 #222（recovery_b 20ac7794）；boot_id b9326dd2（22:37 经 Android `adb reboot recovery` 重启）；fuxi-health 失败 0。
- 81voltd-sub2 = v5（77460da0）。ModemManager = mm-netlink UAF 修复版 fe35ae9f（863c42bd），旧版备份 `ModemManager.c6b20435`（d42cc108）。
- **两卡 IMS 都 registered**：卡 2 在开机 173 s 注册，并保持了 10 分钟。
- 卡 2 卡在 ctnet 时，换回旧 MM 也救不回（已排除 MM 修复）；重启手机能救回。
- 主仓库 56 项、81voltd 树、内核树都未 commit。

## 下一步
1. 只读核对（交接 §9.1）。
2. 问用户：MM 修复第 3 轮验证，只 kill qmi-proxy 1 轮，观察 10 分钟（§9.2）。
3. 问用户：调 heal（宽限 90 s 太短）和 keeper（§9.3）。
4. 问用户：git commit；查上游 MM 有没有修过（§9.4）。然后回到通话音频。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg、fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线 #222 并跑 fuxi-health。
- 不要单独重启 81voltd；不要带参数运行 81voltd；不要用 `--set-primary-sim-slot`；顺序重启是 stop 81voltd、restart MM、start 81voltd。
- 刷机/重启、替换二进制、restart MM、kill qmi-proxy、建 PDN 试验、ADSP 命令、进 Android、费用操作、git commit/push 都要先问用户。
- 不要重试：卡 2 卡在 ctnet 时再做顺序重启或换 MM 版本（2 次都失败），直接问用户能否重启手机；不要连续 kill qmi-proxy；改 Start Network 参数；不先 Bind Mux。
- adb 用 `tools/platform-tools/adb -s f8286c82`。不要用 `pkill -f`，用 `pgrep -x` 加 PID。`work/claude-logs/android-voice-20261101/` 别提交。报告区分实测与推测。
