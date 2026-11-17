# 当前任务

更新：2026-11-17（Asia/Tokyo）。本文件是当前状态的唯一入口；通用规则见
[AGENTS.md](AGENTS.md)，安装/恢复见[操作文档](docs/内核维护与独立升级.md)。

## 当前指令

- 用户已恢复整机体验工作：GNSS、传感器、亮度/旋转、A2DP、扬声器保护、挂起/续航、NFC。
- 主摄功能开发和相机实机操作仍暂停；本轮未操作相机。蓝牙保持用户关闭状态。
- 主摄恢复后只完善 IMX800；其他镜头、录像、徕卡集成延期。保留共享修复。
- 按最新通过相应验证的版本继续，不例行回退；源码验证与实机结果分别记录。

## 此前整改基准

- 规则统一到 `AGENTS.md`，当前状态集中于本文件；`CLAUDE.md` 改为入口，移除旧回退/审批纪律。
- 删除 23 份任务/目标备份、DSH 完全重复目录、旧复制发布脚本及失效全仓哈希表。
  `work/` 新增输出不再纳入 Git；技术证据、实验依赖和恢复材料保留。详见
  [精简记录](docs/项目精简记录-20261116.md)。
- CI 按路径分流，保留统一汇总及周期全检；脚本检查不再下载内核，内核相关主机测试单独执行。
- 内核 deb 自动保存实际源码树及二进制差异（含新文件），构建前后校验；复用兼容输出。
- 本地：脚本 391 项通过（含未纳入本提交的主摄生成器 4 项；整改提交对应 387 项），
  SuperTerm 62 项、内核音量 1 项、14 份活跃文档链接/差异检查通过；actionlint 1.7.12 通过，
  CI 汇总对所选任务失败、取消、意外跳过及输出缺失的拒绝行为已检查。
  日志：`work/fuxi-cleanup-{scripts,apps}-20261116.log`。
  该整改未执行完整内核构建、主摄编译或实机操作，不据此声称编译提速或功能验收。

## 基准与设备

| 对象 | 最近确认状态 | 验证范围 |
| --- | --- | --- |
| 标准配方 | `kernel/manifest.json`，std2 | 可重建基准，不是手机运行版本 |
| 主摄源码基准 | `imx800-power`，`9fc0f4316bc398ca0be035a4d0d71b71f6138701` | 148 项 KUnit、ARM64 W=1、binding/DTB schema、配方重放通过；未部署 |
| 活跃主摄候选 | `build/linux-camera-imx800-mode`，基于上述提交的未提交修改 | 单模式 V4L2、控制/启停、采集 DT；尚有端点校验缺陷，不可直接部署 |
| 手机运行/默认 | `7.4.0-rc1-fuxi-suspend5`；沿用 cam12order 源码基准及相同 DTB，deb9 恢复文件校验通过 | 返回边界捕捉 LPASS pending；时间补偿仅 6.73 ms，RTC 尚余 19 秒，深睡驻留未验收 |
| 已知设备限制 | tainted=4；启动期互连 sync_state 等提示；BCB 曾残留 `boot-recovery` | 普通重启不保证进入 Android；不自动清除 BCB |
| 支持包 | `fuxi-device 0.20261114.1701`；`fuxi-modem 0.20261116.0917` | modem 恢复修复已部署；本轮升级诊断内核并正常重启，未单独重启桌面 |

## 整机体验进展

- GNSS：增加 `scripts/fuxi-gnss-session.sh --modemmanager [秒数]`，通过标准 D-Bus
  进行有界 GPS_RAW 启停及配置恢复，不读取坐标。15 秒实机会话 completed，清理恢复仅 lac-ci、
  signals=no。原始 LOC 20 秒收到 20 条 IN_PROGRESS、19 条卫星报告，仍是 0 有效位置/卫星 fix。
  MM 首次缺失 GPS_RAW 能力本轮再次出现，调试重启后恢复，根因未修；临时调试配置已清除。
  新会话选项已随 modem `0.20261116.0917` 安装；本轮未重做定位，不计有效 fix。
- 挂起：`scripts/fuxi-suspend-check.py` 等待完整 logind 周期后清理 RTC，超时保留保护闹钟；
  捕获全 IRQ、设备 PM、RPMh/AOSS/SMP2P、丢失计数及只读平台睡眠统计。PSCI/GIC/PDC
  边界诊断和配方见 `kernel/experiments/suspend/`。最新 `suspend5` 增加 IPCC 只读快照：
  每次检查 CONFIG 的 CLEAR_ON_RECV_RD 位，关闭时才读 RECV_ID；移除与读取用 raw lock
  同步，不写寄存器/应答 IRQ。进入前无 pending（0xffffffff），cpu_suspend 返回后、IRQ
  handler 前为 0x30000，即 LPASS client3/signal0；两侧 CONFIG=0。随后 IRQ13→IRQ230
  LPASS glink，与早期快照一致，但不能据此确认最早唤醒源。唯一一次 20 秒测试 logind
  周期 1.998857 秒，时间补偿 6.730 ms，GIC 物理计数器区间 4.970 ms，RTC 尚余 19 秒。
  APSS +1、记录 2.240 ms，AOSD/CXSD/DDR 仍全零；不能验收整机深睡或比较功耗改善。
  `cpu_ret=0/firmware_returned=0`，GIC after 仅 SPI261 pending；PDC 仍直通、pin1/109
  使能且配置未变。8 核无丢失，success=1/fail=0；USB/PCIe L2 错误仍在（LTSSM 0x12）。
  RTC/trace 和临时安装配置已清理，自动挂起关闭。离线审计再次确认 syscore 9 条 sleep、
  9 条 wake 命令；cmd-db 映射的 sleep
  BCM 投票 valid/x/y 全零，未观察到非零 PM 回调错误或 AOSS send。USB/PCIe L2 提示
  不直接中止回调，CONTROL_TCS=0；这些结果仍不能证明固件执行投票或排除硬件低功耗阻塞。
  IPCC/glink 使用 IRQF_NO_SUSPEND，不能用 wakeup disabled 排除它们。本候选只测试一次。
- MM 恢复修复：重建授权 cancellable、恢复 QRTR 扫描，处理异步旧回调及多设备清理。
  维护补丁已纳入配方并重放，手机 ARM64 原生构建记录真实软件环境。升级后同一 MM PID、
  NRestarts=0 自动重建 modem、恢复 connected/home，移动接口绑定 HTTPS=200；手工扫描成功。
  恢复期间未人工扫描或重启来促成连接；该次通过不代表多轮稳定性验收。
  suspend5 恢复时 MM 自动重建 modem 和 bearer；总状态仍 registered，但存在 connected
  静态 IPv4 bearer，绑定接口、校验证书的 HTTPS=200。此前按总状态 connected 筛选的
  检查因此失败，不能记作完整 MM 恢复通过；需修复状态与 bearer 不一致，无人工重连。
- 恢复检查：WiFi HTTPS、六类传感器实际采样通过，失败服务 0；健康检查唯一失败为用户关闭蓝牙。
  加速度/磁场/陀螺仪仍有安装矩阵回退警告；轴向、标定、自动旋转和光照动态未验收。
- 功耗：灭屏、USB 充电下 10 秒全核平均忙碌 0.16%，电量 81%；这不是电池续航或功耗改善证据。
  A2DP 需要耳机；CS35L41 扬声器保护仍缺标定验收；NFC 尚无设备通路。
- 验证：GNSS 定向 22 项、全脚本 417 项通过（含未提交主摄生成器 4 项）；挂起工具 8 项
  覆盖请求入队、迟到/完整信号、超时保留 RTC、包含睡眠的期限、未预选 IRQ/丢失计数、异常
  清理及平台统计缺失/权限失败，禁止触碰会发送 QMP 的 `ddr_stats`；新增只读 `ddr` 和
  cmd-db 快照实机读取无错误，不触发挂起且 RTC/trace 未变。离线审计新增 10 项，覆盖阶段
  边界、BCM 解码、丢失/缺失证据和拒绝多周期；原始证据重放通过。日志位于
  `work/suspend-platform-audit-tests-20261117.log`、`work/suspend-readonly-snapshot-20261117.log`，
  审计结果为 `work/suspend4-offline-audit-20261117.json`。
  MM 测试编译真实 watcher 和授权/清理回调，覆盖 10 个异步生命周期场景；来源/依赖检查、原生编译及打包通过。
  实机证据在 `work/gnss-session/`、`work/whole-device-*-20261116.*`、`work/mm-resume-*-20261116.log`，不提交原始日志。
  内核诊断开启/关闭及 PDC 模块/禁用配置的相关对象 ARM64 W=1、完整 deb 构建、包载荷/
  来源及配方树重放通过；新补丁 checkpatch 0 错误、1 项新增头文件提醒，未变更 DTB。
  IPCC 内建/模块/禁用和诊断关闭配置 ARM64 W=1、完整 suspend5 deb、Image/config/DTB
  载荷核对和四补丁配方重放通过；新补丁 checkpatch 0 错误、1 项新增头文件提醒。
  最新诊断证据在 `work/fuxi-suspend5-20261117/`、`work/suspend5-*-20261117.log`；
  恢复后蜂窝接口 HTTPS=200、WiFi HTTPS 和六类传感器采样通过，关键服务 PID 未变、NRestarts=0。
  技术细节见 [GNSS 验收](docs/GNSS按需会话验收-20261108.md)和
  [挂起证据](docs/驱动初始化与功耗验收-20261108.md#普通挂起与恢复检查)。

### 整机下一步

1. 继续提前恢复定位：suspend5 已在 cpu_suspend 返回边界捕捉 LPASS pending，仍不能归因；
   下一步核对 LPASS glink 通道/命令及挂起前未完成请求，设计仅记录协议元数据的有界追踪，
   判断是否有可关联的通信；同时核实 AOSD/CXSD/DDR 统计有效性。Linux 零 BCM sleep 投票
   不排除硬件阻塞，L2 提示仍在。跟进 MM registered 与 connected bearer 不一致。
   不重复相同挂起、不关闭基带或屏蔽 IRQ。
   每个新候选保留准确来源及恢复入口；MM 还需多轮稳定性验证，自动挂起保持关闭，不开始
   续航验收。
2. GNSS 首次能力、室外有效 fix、GeoClue；现场动作验证传感器轴向、亮度/旋转，耳机验证 A2DP。
3. 核对 CS35L41 保护固件/标定与安全音量；识别 NFC 控制器和接线后实现驱动通路。

## 暂停的主摄候选

主摄候选已通过 377 项脚本回归、主摄对象及两份 DTB 的 ARM64 W=1、binding 检查。
新增 KUnit 仅构建，未执行；实际 DTB schema、完整包和实机采集未完成。
日志：`work/imx800-mode-{script-tests,arm64,schema,kunit-build}-20261116.log`。
本地新增 `scripts/fuxi-imx800-mode.py` 及测试保留，未纳入整改提交；载荷保留于
`work/fuxi-imx800-mode1.bin`，来源/摘要见[最后阶段记录](docs/主摄暂停交接-20261116.md)。

共享底层最近实机证据：OV13B10 单次 RGB 60 帧及停机资源归零通过，第二轮仍发生
MM0 超时并影响 UFS/CPU/GPU；之后正常重启已恢复 Linux。该结果不计为 IMX800 出图，
也不计为生命周期通过，见[实机证据](docs/相机CPAS启动顺序候选-20261115.md#cam12order-实机结果)。

## 下一步（主摄恢复后）

1. 修正 `fuxi_imx800_load_mode()` 将 DT `bus-type=1` 与内部 V4L2 枚举混比的问题，
   补查 trio line order，审查启停/PM 清理。在现有候选继续，不新建实验链。
2. 执行相关定向检查，保存准确源码/配置/包来源，部署后检查绑定/身份并做有界单次 RAW。
3. 按实际结果推进停流、再次开启、RGB、取景、JPEG；最后执行稳定性验收。

真实 Bayer、PLL/settle、辅助流过滤、增益换算及固定焦点可辨认范围待验证。
主摄尚无实际帧；最终条件见[基础相机目标](docs/基础相机目标.md)，
技术入口见[主摄计划](docs/主摄接通计划-20261115.md)。

## 后续队列

| 工作 | 剩余问题 |
| --- | --- |
| 通话与充电 | ADSP TX `GRAPH_START -22`、通话无声；持续快充/温度验证 |
| 应用 | 交互审批传输/恢复、DSH 续接与设备迁移、三驱动真实推理 |
| 整机 | GNSS 有效 fix、传感器轴向、自动亮度/旋转、A2DP、扬声器保护、挂起/续航、NFC |
| 发布 | 适用的许可、签名、来源与维护审核；不阻塞本地实验 |

完整功能边界见[覆盖核查](docs/功能覆盖核查-20261108.md)和
[相机矩阵](docs/相机功能完成矩阵.md)。历史经过通过 Git 历史及已有报告查阅，
不继续向本文件追加长篇成果清单或复制日期交接。
