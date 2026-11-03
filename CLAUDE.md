# linux-android 项目上下文纪律

## 上下文控制

- **不到 150k token 不写 handoff**。同一条调试线索（同一个驱动/同一个问题）连续干，不要中途 handoff。只在切换大方向（例如从 TX 图重建切到下一个子系统）或明确要休息时才写。
- **上下文 > 200k**：先问"是继续还是 /handoff"，不要自己决定开新会话。
- 每完成一个可交付子任务（rtest 通过 / 一个功能确认）才问一次"要不要写 handoff"，不要每隔几轮就问。

## 大文件 / 日志禁止直接读进主对话

以下文件**必须派子 agent 处理，只回结论**，不得在主对话里 Read 或 cat：

- `work/claude-logs/*/dmesg*.txt`（单文件 40–72 KB）
- `work/claude-logs/voice-shmdump*/voice-shmdump*/*.bin`（每份 32 KB，65+ 份）
- `work/claude-logs/android-voice-*/`（目录 15 MB）
- `work/claude-logs/android-voice-shmdump*/`（目录 1.4 MB）
- `docs/设计-*.md`（单文件 60 KB）
- 任何 > 20 KB 的单文件

子 agent 返回：关键行 / 关键字段 / diff，不要整文件内容。

## rtest 循环里的日志

- `ssh.txt` 正常（≤ 475 字节），可以直接读。
- `summary.txt` 正常，可以直接读。
- `dmesg-diag.txt` / `dmesg-full.txt`：子 agent grep，只取含 `error|fail|ERR|WARN|cache_creation|miid|0x43a` 的行回主对话。

## 模型分工

- **Opus**：需要推理的环节——图结构分析、新思路设计、出错根因判断。
- **Sonnet**：机械循环——改一行头文件 → 编译 → rtest → 看日志是否通过。一个 rtest 循环如无需深度分析，用 Sonnet 跑。

## 本项目红线（摘要，完整见 TASKS.md）

- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- 音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
