# 当前任务

更新：2026-11-19（Asia/Tokyo）。本文件是当前状态的唯一入口；通用规则见
[AGENTS.md](AGENTS.md)，安装/恢复见[操作文档](docs/内核维护与独立升级.md)。

## 当前指令

- 用户已设定目标：能自动完成的发布与内核维护工作全部完成，新内核作为主用版本。
  按标准独立升级配方使用上游 `v7.4-rc3`，14 个补丁重放和 ARM64 deb/载荷校验通过；手机
  当前运行且默认此版本，`suspend6` 是已确认 fallback，bootcount `pending=0`。
- 用户以仓库 admin 身份明确授权发布 `v0.9.0-rc1` GitHub 预发布。范围限于 GPL Linux 内核
  deb 及匹配源码/校验文件；排除固件、校准数据、无再分发许可的 EDK2 Xiaomi 兼容 DTB
  (`build/edk2-msm/Platform/Xiaomi/sm8550/FdtBlob_compat/fuxi.dtb`) 和无关项目组件。
  本机没有维护者 GPG 私钥，按显式批准的无签名预发布流程交付并标明这一点；签名 APT 源和
  稳定版晋级仍需密钥及完整硬件验收。
- [`v0.9.0-rc1` GitHub Release](https://github.com/ilovecnxx/xiaomi13-fuxi-linux-ubuntu26.10-kernel7.3/releases/tag/v0.9.0-rc1)
  已创建并核验：tag 指向提交 `da0d94aa88f37c00e6c3114c91ed238f2927bcaf`，Release 为非 draft
  prerelease，包含内核 deb、对应源码归档和 `SHA256SUMS`。三个远端资产 SHA256 为：deb
  `2f95708f824ee66c89fb778b3d5f076887ccefa78eb4cfdfb20a3cf2645ba138`，源码归档
  `49dede60ce91e84dbd554b404406ed4712ec43bcb168a658609850ef8b9cf13e`，`SHA256SUMS`
  `db3d44638e8dbd894537e2c8fe0500d03fb378e84aebcc9ad2a2c43fecdd6b9a`；远端下载后复核通过。
  不能把无签名预发布写成稳定版或签名发行物。
- 24 小时稳定性、10 次重启及完整整机矩阵尚未完成。RC3 是实验性主用候选，不宣称稳定版。
- 充电有界排查已停止，快充未解决；最新只读状态 92%、Charging，但 USB 仍为 SDP/500mA 档，
  电池电流瞬时为负，不能据此宣称充电或快充修好。当前目标聚焦新内核与预发布，不改充电控制。
- 主摄功能开发和相机实机操作仍暂停；不改蓝牙用户设置，自动挂起保持 `nothing`。
  最新只读检查 BlueZ `Powered=true`，与此前关闭记录不同，原因未确认；本轮未操作蓝牙。
- 主摄恢复后只完善 IMX800；其他镜头、录像、徕卡集成延期。保留共享修复。
- 按最新通过相应验证的版本继续，不例行回退；源码验证与实机结果分别记录。
- SuperTerm 已按用户决定取消：源码、部署入口、CI 检查和维护计划已由提交 `589ede8`
  删除；当前仓库没有可维护的 SuperTerm 文件。历史验收文档和实验 manifest 中的旧名称仅作
  证据保留，不再恢复、测试或继续维护。
- USB 当前只提供到手机 `10.42.0.2` 的网络路径；本次核对未发现活跃 SSH/轮询任务。主机侧文档、
  源码审计和测试可以与其他独立任务并行；涉及手机数据或设备状态的步骤仍单独限界执行。2026-11-19
  02:46 的只读现场快照中，手机运行 `7.4.0-rc3-fuxi-upstream74rc3`、failed units 为 0；音频检查
  0 失败，两个 CS35L41 均已绑定且 B/T 数字音量上限为 816；NFC 类目录不存在，I2C 仅见
  `0-0042`、`1-0040`、`1-0042`，SPI 仅见 `spi0.0`，没有 NFC 相关内核日志。完整厂商 DT
  另有静态 NFC 候选：`i2c@a80000/nq@28` 的 `qcom,sn-nci` 节点，关联 GPIO55 中断、
  GPIO30/12/31 控制脚、1.8V 供电和参考时钟；当前运行内核尚未实例化此节点。
- 标准源码默认树已于 11-19 按 `kernel/manifest.json` 重建并切换至 `f4f8ed8b…`；旧的
  `90afc07d…` 完整保留在 `build/linux-standard-old-20261119/`，相机候选工作树关联已修复。

## 未完成任务清单（2026-11-19）

### 可在主机并行推进

1. DSH 续接与设备迁移（主机入口已完成，设备数据待决定是否迁移）：已加入只读 `audit`、默认
   排除凭据的可校验 `backup`、以及归档 `verify`；分别记录会话日志、附件、工作区、设置、凭据
   和 `DSH_HOME`，并核对用户级 systemd 环境、标准部署路径及回滚对用户数据的影响。11-19 通过
   USB SSH 只读盘点：`/home/fuxi/.dsh` 有 98 个会话文件（39.4 MB）、262 个附件（73.7 MB）、
   50 个 storage 文件（539 KB）和 8 个 profile 文件（8.9 KB）；`/root/.dsh` 另有 4 个
   profile 文件和 1 个 storage 文件，未发现会话/附件目录。只读取目录元数据统计，未复制数据。
   标准部署使用用户 `~/.dsh`；迁移前仍需由用户决定数据范围。主机验证已通过 DSH 定向 9 项、
   脚本 CI 478 项、14 份活跃文档链接检查和 `git diff --check`。
2. 发布维护：已补强 `scripts/fuxi-release-check.py`，现在常规 Git 工作树中的未跟踪文件也会
   阻止发布，并由回归测试覆盖；仍需补齐适用许可、来源、维护审核和签名条件。当前
   `v0.9.0-rc1` 仍是无签名预发布，不能晋级稳定版。
3. 通话/音频源码与配置审计：处理 ADSP `GRAPH_START -22`、通话无声，以及 CS35L41 保护固件
   和标定缺口；硬件回归仍需设备条件。主机侧语音图/VCPM 缺口审计已完成；02:46 只读音频检查
   通过（声卡、播放 PCM、两个功放绑定和 816 音量上限），但启动日志仍有既有 `q6apm` 命令超时，
   `MultiMedia4 Capture` 没有后端路由；这些结果不能视为通话音频通过。下一步是整理可验证的
   Android 语音图来源和端点配置，不能凭猜测向 ADSP 下发参数。

### 需要手机或实机验收

4. 内核稳定性：完成 24 小时稳定性、10 次重启和完整整机回归矩阵；确认主用版本与 fallback
   在多轮重启后的恢复行为。
5. 挂起实验：在稳定净充电且电量至少 20% 后，才安装已构建的 `suspend7`，核对默认/恢复入口，
   再做一次有界 20 秒 RTC + QRTR/PMIC 元数据采集；同时核实 AOSD/CXSD/DDR 统计有效性和
   ModemManager bearer 状态差异。自动挂起保持关闭。
6. GNSS/定位：保留用户隐私设置，完成定位开启后的授权链路、室外有效 fix、误差/UTC、功耗和
   恢复验收；桌面 GeoClue 链路尚未完成。
7. 桌面与传感器：用 Phosh 实际亮度滑块完成亮度偏移、明暗响应和熄屏/亮屏验收；完成传感器轴向、
   标定、陀螺仪单位及自动旋转验证；补做 A2DP 耳机验收。
8. 设备功能：把完整厂商 DT 中 `i2c@a80000/nq@28` 的 `qcom,sn-nci` NFC 候选映射到当前
   内核并实现通路；通话无声和持续温度/充电验证仍未完成。02:46 实机只读核对仍未发现
   `/sys/class/nfc`、NFC 日志或已实例化的 NQ-NCI 设备，因此候选的总线映射、GPIO、电源、
   时钟和驱动绑定都需要实机验证，不能仅凭静态 DT 宣称 NFC 已通过。

### 暂停或明确停止

9. 主摄 IMX800：现有候选仍有端点校验和实机首帧缺口；恢复开发后按既定三步继续，当前不部署。
10. 快充：当前已确认可以净充电但快充未修复；因缺少可验证的固件分支和 PD PHY 失败原因，
    已按停止条件结束排查，不追加控制写入、认证、选压或其他候选部署。

## 此前整改基准

- 规则统一到 `AGENTS.md`，当前状态集中于本文件；`CLAUDE.md` 改为入口，移除旧回退/审批纪律。
- 删除 23 份任务/目标备份、DSH 完全重复目录、旧复制发布脚本及失效全仓哈希表。
  `work/` 新增输出不再纳入 Git；技术证据、实验依赖和恢复材料保留。详见
  [精简记录](docs/项目精简记录-20261116.md)。
- CI 按路径分流，保留统一汇总及周期全检；脚本检查不再下载内核，内核相关主机测试单独执行。
- 内核 deb 自动保存实际源码树及二进制差异（含新文件），构建前后校验；复用兼容输出。
- 本地：脚本 391 项通过（含未纳入本提交的主摄生成器 4 项；整改提交对应 387 项），
  内核音量 1 项、14 份活跃文档链接/差异检查通过；actionlint 1.7.12 通过，
  CI 汇总对所选任务失败、取消、意外跳过及输出缺失的拒绝行为已检查。
  日志：`work/fuxi-cleanup-scripts-20261116.log`。
  该整改未执行完整内核构建、主摄编译或实机操作，不据此声称编译提速或功能验收。

## 基准与设备

| 对象 | 最近确认状态 | 验证范围 |
| --- | --- | --- |
| 标准配方 | `kernel/manifest.json`，上游 `7.4.0-rc3` | 归档 SHA256 `95c0eb4a…`，上游根树 `e5a64382…`，14 个补丁重放；最终树 `f4f8ed8b…`；包 `linux-image-7.4.0-rc3-fuxi-upstream74rc3` SHA256 `2f95708f…` |
| 主摄源码基准 | `imx800-power`，`9fc0f4316bc398ca0be035a4d0d71b71f6138701` | 148 项 KUnit、ARM64 W=1、binding/DTB schema、配方重放通过；未部署 |
| 活跃主摄候选 | `build/linux-camera-imx800-mode`，基于上述提交的未提交修改 | 单模式 V4L2、控制/启停、采集 DT；尚有端点校验缺陷，不可直接部署 |
| 手机运行/默认 | `7.4.0-rc3-fuxi-upstream74rc3`；fallback `7.4.0-rc1-fuxi-suspend6` | 2026-11-18 安装并正常重启；bootcount `pending=0`；新内核和 fallback 的 Image、DTB、initrd、cmdline 校验通过 |
| 离线挂起候选 | `7.4.0-rc1-fuxi-suspend7`，六补丁树 `1c836d26101571c017fe6bdfdf94e45c11c83091` | QRTR RX/候选 QMI 头追踪，完整包和主机验证通过；比当前主用内核旧，保持离线实验包，不取代 rc3 |
| 已知设备限制 | tainted=4；启动期互连 sync_state 等提示；BCB 曾残留 `boot-recovery` | 普通重启不保证进入 Android；不自动清除 BCB |
| 支持包 | `fuxi-device 0.20261114.1701`；`fuxi-modem 0.20261118.2101`；`fuxi-geoclue 0.20261118.2301`；`fuxi-desktop 0.20261118.1901` | 原生 ARM64 包已安装；MM `.2101` 的 GPS_RAW 按需启停及恢复通过；GeoClue `.2301` 修复权限撤销期间显式停止后意外恢复，桌面位置权限 false |

## 整机体验进展

- 自动亮度：当前 Phosh `0.59~alpha1-1` 提供原生亮度控制，`HasBrightnessControl=true`、
  `ambient-enabled=true`，自定义服务此前也在运行。desktop `0.20261118.1901` 移除 Phosh
  对自定义服务的 Wants/PartOf 关系，Denial/Plasma 仍使用自定义服务；部署后只停止重复服务，
  不重启桌面。包 SHA256 为
  `6bc4b10a73a6852d5ded7a2781f25b7eb99c088bc76e9508ce84f57258881ce9`。
  17 项相关测试及全脚本 455 项通过（含 4 项未提交主摄测试）；载荷与源码一致，包校验正常。
  现场原生接口可用、自定义服务 inactive；Phosh PID959、SensorProxy PID760 未变，
  failed=0，屏幕保持关闭、亮度 153/4095，用户亮度设置、蓝牙关闭及自动挂起 nothing 均保留。
  此前的手动让出修复仍保留在 Denial/Plasma 路径：重新领取取现有 60 秒期限与 2 秒稳定期的
  较晚值，真实 SensorProxy + 临时背光目录验证通过，但不能代替物理屏幕验收。
  本轮实屏测试中曾直接写 sysfs 并观察到亮屏后提前变化，未定位 writer；直接写 sysfs 不等同
  Phosh 滑块，不能据此归因或认定原生控制缺陷。现场明暗、实际滑块及反复亮灭屏仍待验收。
  当前策略面向已确认有原生控制的 Phosh；旧版 Phosh 无自动探测回退，未验证。
  证据见 `work/brightness-native-{regression-before,regression-after,package,install,final,script-ci}-20261118.log`；
  详见 [亮度验收记录](docs/Phosh自动亮度验收-20261109.md)。
- GNSS：增加 `scripts/fuxi-gnss-session.sh --modemmanager [秒数]`，通过标准 D-Bus
  进行有界 GPS_RAW 启停及配置恢复，不读取坐标。15 秒实机会话 completed，清理恢复仅 lac-ci、
  signals=no。原始 LOC 20 秒收到 20 条 IN_PROGRESS、19 条卫星报告，仍是 0 有效位置/卫星 fix。
  MM 首次缺失 GPS_RAW 能力此前再次出现，调试重启后恢复，根因未修；临时调试配置已清除。
  11-18 修复 LOC 解锁无响应时仍解析空响应的错误；带 fuxi marker 的 LOC 客户端分配失败现在记录普通警告。
  更新前 rc3 上首次启动的 MM 只有 lac-ci，独立 LOC 查询成功；安装 `.1801` 后普通重启 MM，
  GPS_RAW 恢复，蜂窝 connected/home。标准 15 秒启停及清理通过，未读取坐标。33 项定向及全脚本
  460 项通过。下次自然启动先查能力和 LOC 分配警告；本轮没有证明首次能力缺失的根因。
  原始 LOC 诊断已增加同一成功卫星定位报告的水平误差、UTC 和置信度计数。45 秒及 10 秒
  实测均无有效定位；后一次 9 条卫星状态均缺旧列表，不能推断无卫星。462 项脚本测试通过。
  增加扩展列表识别后，15 秒实测 14 条卫星报告均缺少旧版和扩展列表，解码错误为 0；
  15 条位置报告仍均为 IN_PROGRESS，463 项脚本测试通过。下次在开阔环境对照卫星报告，
  并确认有效定位的水平误差与 UTC；GeoClue 桌面链路尚未验收。
  2026-11-18 追加 120 秒原始 LOC 会话：收到 120 条位置报告，全部为 IN_PROGRESS，
  0 条有效位置、0 条卫星 fix，119 条卫星报告无可解码列表，未见解码错误；Stop/release/close
  均成功，清理后状态恢复。当前自然状态随后复核为单一 LOC 节点、GPS_RAW 能力可用且仅
  `3gpp-lac-ci` 启用。证据为忽略目录 `work/gnss-session/20261118-203952.json` 和
  `work/gnss-audit/20261118-204424.json`。这进一步确认启停/清理路径正常，但不能替代
  开阔环境的有效 fix、误差和 UTC 验收。
  随后的自然启动日志只读核对未见 LOC 分配或能力加载警告；首次仅有 lac-ci 的 PID746 早于 `.1801`
  诊断告警部署，不能据此判断 LOC 分配结果。`.1801` 后启动的 PID23415 也没有分配警告，当前
  GPS_RAW 能力正常、桌面定位仍关闭。常规 QMI 探测关闭临时端口，正式初始化按序分配 LOC 后才进入
  父初始化；首次缺失现场日志不足，根因仍未定位，也没有依据改初始化顺序。详见 GNSS 验收记录。
  21:26 的只读审计再次确认单一 LOC 节点、QMI 属性可读、`gps-raw` 能力存在且仅 `3gpp-lac-ci`
  启用；未启动定位会话，`satellite_fix` 仍为 `not_tested`。USB SSH 当时不可达，改走既有 Wi-Fi
  地址成功，证据为 `work/gnss-audit/20261118-212610.json`。
  21:29 的标准健康检查在 rc3 运行约 20 小时后通过，基础服务、Wi-Fi HTTPS、热区、触摸和传感器
  采样均正常；加速度计、磁力计、陀螺仪仍报告安装矩阵回退，陀螺仪单位与轴向尚未验收。证据为
  `work/claude-logs/health-20261118-212901.log`；运行时审计同时确认 TUN 可用，KVM/QEMU 未配置，
  与手机整机目标无关，证据为 `work/runtime-audit/20261118-212901.json`。
  21:35–21:42 的只读蜂窝复核显示 MM 为 `connected/home/attached`，未复现总注册状态异常；
  两个 bearer 都被 MM 标为 connected。`qmapmux0.1` 是 `ctnet`，有 IPv4/IPv6 地址及默认路由，
  绑定该接口的 IPv4/IPv6 证书校验 HTTPS 均为 200。`qmapmux0.0` 是 APN `IMS`、profile 2，
  仍为 DOWN、无地址和默认路由。默认数据链路正常；IMS bearer 的 MM connected 与内核接口
  状态差异仍待确认其预期语义，未断开、重连或重启。内核仍为
  `7.4.0-rc3-fuxi-upstream74rc3`，21:49 时运行约 20 小时 31 分，boot ID 未变。
  随后只读核对显示两个 `qmapmux` 设备均为 NetworkManager `unmanaged`，因此没有 NM 活动连接；
  `qmapmux0.1` 仍由 MM 直接配置并保持双栈可用。IMS bearer 的 MM 配置为 IPv6-only，QMI 日志中
  的 IPv4 建链被 `pdn-ipv4-call-disallowed` 拒绝；MM 仍报告 IPv6 配置，但内核接口没有地址或
  路由且保持 DOWN。该状态仍不足以确认 IMS bearer 的预期内核语义，继续不做重连、断开或重启。
  源码审计确认 MM 创建 mux 子接口后，连接阶段显式置 UP 的对象是主数据接口；bearer 的
  D-Bus `Connected`/IP 配置更新不检查子接口管理状态。内核 rmnet 子接口初始化未置 `IFF_UP`；
  81voltd 将地址经 IMSD 回报给基带，没有执行主机地址或路由配置。独立的 `fuxi-datacard`
  直接 WDS 数据路径会显式配置主机接口，但不负责这里的 MM IMS bearer。因此这些路径能解释
  状态为何可能分离，不能证明 IMS 子接口保持 DOWN 一定符合预期；源码未发现足以支持修复的
  已证实缺陷。设备 uptime 达 24 小时后再只读复核；此前不改接口、bearer 或服务状态。
  22:29 的只读复核仍见 MM `connected`、`qmapmux0.0` DOWN 且无主机地址/路由，同时 IMSA 为
  `registered`，SMS、语音和视频服务均 `available`。这仅记录两者在该时刻并存，不足以判断
  IMS 子接口的预期状态，继续不据此修改 MM、rmnet 或 81voltd。
  22:33 经 Wi-Fi 对手机的只读复核显示仍为 `7.4.0-rc3-fuxi-upstream74rc3`，本次启动 uptime
  约 21 小时 15 分；五个相关服务均 active 且 NRestarts=0。MM `/Bearer/0` 仍为 IMS、profile 2、
  IPv6、`connected=yes`，但 `qmapmux0.0` 仍 DOWN、无地址/路由；`/Bearer/1` 的 `qmapmux0.1`
  仍为双栈并有默认路由，IMSA 仍 registered、SMS/voice/video 均 available。24 小时门槛尚未到，
  不据此做接口、bearer 或服务变更。
  23:00 的中间只读复核仍为同一 boot ID 和内核，设备 uptime 21 小时 42 分。ModemManager、
  81voltd、rmtfs、keeper 与 SIM2 消息服务均 active、NRestarts=0，systemd failed units 为空；
  modem 为 5G/home/attached。`/Bearer/0` 仍是 IMS、profile 2、IPv6、connected，MM 返回静态
  IPv6 配置，计数器显示 attempts=1、duration=12089 秒；其 `qmapmux0.0` 仍 DOWN 且没有主机地址
  或路由。`/Bearer/1` 的 `qmapmux0.1` 保持双栈、UP 并有默认路由；两个 mux 设备均 unmanaged。
  IMSA 为 registered，SMS/voice/video 均 available。上述状态仅记录为同时存在，不能确定 IMS
  子接口预期管理状态；24 小时门槛未到，未改设备状态。
  主机候选已补齐同一 LOC 报告的水平误差传递：MM 回调和私有 libmm-glib 字典往返保留
  `x-fuxi-horizontal-radius-m`，仅输出有限正值；缺失不补值，后续 NMEA 不继承旧半径。
  固定源码重放、amd64 MM/私有库构建、真实库 2 个回归用例和 463 项脚本测试通过。
  新候选增加同报告 `x-fuxi-utc-ms`（uint64）；固定 GeoClue 2.7.2 与 Ubuntu 2ubuntu3 三个补丁，
  无 NMEA 时消费 RAW 字典中的坐标、实测误差和完整 UTC，拒绝会话前、重复、过期和过远未来报告。
  RAW 停止恢复原 RAW/signals 设置并保留其他当前源；覆盖启动中停止、取消、失败和设备消失。
  配方重放与已测树一致，amd64 MM/私有库及 GeoClue 完整守护进程构建通过；GeoClue 4 个解析与
  10 个隔离 D-Bus 用例、MM 扩展 3 个用例及现有 common/pco 测试、463 项脚本测试通过。
  后续主机回归复现并修复同步停止后立即重启残留 RAW、其他源停止清掉原有 RAW 的问题；
  RAW 新会话读取实际配置，启动中重启排队，后续停止/取消可撤销排队请求；最后一个内部源
  停止时补做延后的 signals 恢复。完整 GeoClue 构建、4 个解析及 18 个隔离 D-Bus 用例通过，
  包括暂缓 Setup 返回后的重启/设备消失与两种停止顺序。进一步复现并修复蜂窝先启动时 signals
  基线错误，所有内部源现在共用启动队列、原始源位和 signals 基线；恢复失败保留基线并阻止
  后续启动，再次停止可重试。修复经其他源恢复成功后 RAW 会话标记残留；实际 enable 路径、
  重叠启停/取消/断连、延迟状态变更及失败恢复共 34 个 D-Bus 用例、4 个解析用例与完整守护
  进程构建通过。外部进程同时改写相同配置的冲突不具备原子仲裁。
  ARM64 GeoClue 和配套 MM/私有库现已构建并安装 `.2101`；原生 GeoClue 4 个解析及 34 个
  D-Bus 用例、私有库扩展/common/pco 测试及全脚本 469 项通过。GeoClue 通过服务私有挂载
  使用候选程序，保留发行版执行路径、配置及授权策略；沙箱内启动路径验证通过，AppArmor
  强制访问控制未验证。安装不自动启动服务，仅正常重启 MM；Phosh PID894 未变。
  安装后 15 秒 MM 会话 completed，恢复仅 lac-ci、signals=no；GeoClue inactive，桌面位置
  权限 false。绑定实际数据接口 qmapmux0.1 的 HTTPS=200，无人工重连；此前请求误绑到无地址、
  DOWN 的 qmapmux0.0 而失败，不能据此判定蜂窝回归。蓝牙只读检查 Powered=true，未改设置。
  GeoClue 后续 `.2201` 已安装：修复 GetAll 尚未返回时请求已停止/取消却仍发出硬件 Setup 的
  问题；7 个新用例覆盖 GPS/蜂窝、立即重启及保留其他活动源，原有 Setup 阶段测试改为等待
  请求实际到达后再取消。amd64/ARM64 均为 4 个解析及 41 个 D-Bus 用例通过，源码重放、完整
  构建与全脚本 469 项通过。安装前后 Phosh PID894、MM PID29044、配置/授权策略均未变，
  GeoClue inactive，定位 false、自动挂起 nothing。后续以普通用户连接真实 GeoClue/Phosh
  验证关闭定位的拒绝路径：Start 返回用户关闭定位的 AccessDenied，客户端始终 inactive、
  Location 为 `/`；有效总线监测记录 Start=1、MM Setup/GetLocation/位置更新均为 0。
  已确认运行程序是 `.2201` 候选，MM 源位/signals 和桌面/MM PID 未变；删除测试客户端后
  正常停止 GeoClue，恢复 inactive。只验证关闭分支，未开启定位或验证获准后的采集链路。
  GeoClue `.2301` 已安装：新增 `scripts/tests/integration/geoclue_raw.py`，在私有 D-Bus 上运行完整
  GeoClue 守护进程、模拟 MM/Agent 和客户端。复现权限撤销后客户端显式 Stop，权限恢复时
  仍自动恢复采集的问题；`geoclue-explicit-stop.patch` 清除该客户端的自动恢复标记。
  主机完整守护进程回归修复前失败，修复后 amd64/ARM64 均通过：3 次有效更新、6 次 Setup，覆盖关闭拒绝、
  RAW 传递、无效报告拒绝、停止恢复、重启新鲜度及权限撤销/恢复；显式 Stop 后不再恢复。
  此测试使用非沙箱客户端，只验证全局 Agent 权限和生命周期，不验证逐应用授权或真实桌面。
  最终配方无偏移重放，`explicit-stop-final/geoclue` 与已测源码一致；amd64/ARM64 原有
  4 个解析及 41 个隔离 D-Bus 用例、全脚本 469 项通过。安装前校验包来源及 rc3/suspend6
  引导文件；安装后普通用户真实 Start 仍因关闭定位被拒绝，MM Setup/GetLocation/位置更新
  均为 0，已核验运行的是 `.2301`。清理后 GeoClue inactive，定位 false、自动挂起 nothing、
  蓝牙设置及 Phosh PID894/MM PID29044 未变。包哈希和证据见 GNSS 验收文档；下一步
  在保留用户隐私设置的前提下验证定位开启后的客户端授权，并完成室外有效 fix、误差/UTC、
  功耗及恢复验收；不得把原生隔离测试当成桌面或卫星验收。记录见 GNSS 验收文档及
  `work/{loc-radius,geoclue-raw}/`；主机测试不代表桌面定位已可用。
- 挂起：`scripts/fuxi-suspend-check.py` 等待完整 logind 周期后清理 RTC，超时保留保护闹钟；
  捕获全 IRQ、设备 PM、RPMh/AOSS/SMP2P、丢失计数及只读平台睡眠统计。PSCI/GIC/PDC
  边界诊断和配方见 `kernel/experiments/suspend/`。当前 `suspend6` 在已有 IPCC 只读快照
  上增加默认关闭的 PMIC TX/TX_done/RX 头追踪，仅读取公共 12 字节头，不读取载荷。
  `--pmic-metadata` 要求 3 个 PMIC 事件及 13 个 glink 事件全部可用，先记录 5 秒基线
  再设 RTC。只做一次 20 秒测试：logind 周期 6.355196 秒，MONOTONIC 2.113861 秒，
  时间补偿 4.241335 秒，GIC 物理计数器区间 4.239237 秒，RTC 尚余 14 秒。
  IPCC before 无 pending（0xffffffff），返回后、handler 前为 0x20000，即 MPSS
  client2/signal0；两侧 CONFIG=0。随后 IRQ13→IRQ231，首先收到 MPSS IPCRTR 92 字节数据。
  基线两条 220 字节 RX 头为 BATTMGR owner32778/type1/opcode0x50；恢复后无此类 RX，
  只有更晚的 0x30 电池查询。此前 LPASS/PMIC 恢复模式未复现，不能视作所有早醒的共同原因，
  也不能用本轮头信息回填上轮未记录的头。TX_done 只表示传输返回，不证明固件请求完成。
  APSS +1、累计增加 4.235744 秒；AOSD/CXSD/DDR 仍全零，统计有效性未核实。
  与先前测试相比追踪/时序不同，不能验收深睡或比较功耗改善，最早唤醒源仍未确认。
  `cpu_ret=0/firmware_returned=0`，GIC after 仅 SPI261 pending；PDC 仍直通、pin1/109
  使能且配置未变。8 核无丢失，success=1/fail=0；USB/PCIe L2 错误仍在（LTSSM 0x12）。
  RTC/trace 和临时安装配置已清理，自动挂起关闭。离线审计再次确认 syscore 9 条 sleep、
  9 条 wake 命令；cmd-db 映射的 sleep
  BCM 投票 valid/x/y 全零，未观察到非零 PM 回调错误或 AOSS send。USB/PCIe L2 提示
  不直接中止回调，CONTROL_TCS=0；这些结果仍不能证明固件执行投票或排除硬件低功耗阻塞。
  IPCC/glink 使用 IRQF_NO_SUSPEND，不能用 wakeup disabled 排除它们。未重复同配置挂起。
  新离线候选 `suspend7` 在 QRTR 校验后、socket 投递前记录包类型/路由，并仅对非控制端口
  DATA 检查 7 字节 QMI 公共头的类型与精确长度，记录候选类型/消息编号/长度；不读 TLV、
  事务号或业务载荷。匹配不证明 QMI、服务身份、投递成功或唤醒因果。`--qrtr-metadata`
  要求 QRTR 和 13 个 glink 事件，PMIC 仍需显式开启；缺事件在设 RTC 前拒绝并清理。
  审计按 trace 行顺序分类并拒绝不一致候选，不能回填旧 trace 的 92 字节消息内容。
- MM 恢复修复：重建授权 cancellable、恢复 QRTR 扫描，处理异步旧回调及多设备清理。
  维护补丁已纳入配方并重放，手机 ARM64 原生构建记录真实软件环境。升级后同一 MM PID、
  NRestarts=0 自动重建 modem、恢复 connected/home，移动接口绑定 HTTPS=200；手工扫描成功。
  恢复期间未人工扫描或重启来促成连接；该次通过不代表多轮稳定性验收。
  suspend5 恢复时 MM 自动重建 modem 和 bearer；总状态曾为 registered，但存在 connected
  静态 IPv4 bearer，绑定接口、校验证书的 HTTPS=200。此前按总状态 connected 筛选的
  检查因此失败，不能记作完整 MM 恢复通过；suspend6 本次总状态 connected、绑定 HTTPS=200，
  无人工重连，但这不证明此前状态与 bearer 不一致的问题已修复。
- 恢复检查：WiFi HTTPS、六类传感器实际采样通过，失败服务 0；健康检查唯一失败为用户关闭蓝牙。
  加速度/磁场/陀螺仪仍有安装矩阵回退警告；轴向、标定、自动旋转和光照动态未验收。
- 功耗：灭屏、USB 充电下 10 秒全核平均忙碌 0.16%，电量 81%；这不是电池续航或功耗改善证据。
  A2DP 需要耳机；CS35L41 扬声器保护仍缺标定验收；NFC 尚无设备通路。
- 充电优先：11-18 USB 数据连接不在主机上，已通过 WiFi 连接；初查电量 9%、PD 输入
  约 4.90 V / 1.397 A，电池净电流 +1.481 A。随后 50 秒电荷 308→325 mAh，六次净电流
  均正（+1.388 至 +1.569 A），温度 20.6–20.7°C，确认该窗口净充电，不能归功于本轮软件
  修改。固件 ICL 仍 1.5 A；新读取的运行日志含 `RP_SHORT_VBUS` 和 `icl_ma=1500`，
  AUTHENTIC=1、PD_VERIFED/FASTCHGMODE=0，快充未修复。CC_SHORT_VBUS=1 不能直接认定
  物理短路或清零绕过。UCSI 电压为零、能力电流与 battmgr 实测不同，不能用来验收实际输入。
  新增只读 `scripts/fuxi-charge-check.py`，以连续在线、供电类型、净电流和电荷趋势共同判断，
  不凭 Charging 状态通过；查询会发送 PMIC GET。证据见 `work/charging-priority-*-20261118.*`。
  工具实机 50.017 秒通过：电荷 394→419 mAh、电量 12%→13%，输入 6.844–6.862 W；
  末查 13%、21.5°C、失败服务 0、RTC/trace 无残留，专用日志通道 override 为空。
  新增工具 6 项定向测试通过；本轮未改内核/限流/认证，未重启或挂起。
  随后的 UCSI 长度核验使用新增 `scripts/fuxi-ucsi-pdos.py`：同一次 GET 经独立追踪实例
  的 ret 过滤验证实际长度，拒绝缺失、多条、歧义或丢失证据；不依赖固定响应缓冲区猜长度。
  GET_CAPABILITY 返回 16 字节，源 PDO 首批 16 字节、偏移 4 尾页 0 字节；固件当前报告
  4 个固定档位：5 V/3 A、9 V/3 A、15 V/3 A、20 V/3.25 A，未返回 PPS。
  PDO_DETAILS 未声明，Linux 因而跳过自动读取，解释 sysfs 无 source-capabilities；不证明
  这是 5 V 合同的原因。此次查询没有选择 PDO，也不能据缓存能力认定充电头型号或物理 PPS
  支持。末查 22%、电荷 742 mAh、净电流 +0.922 A、23.4°C，输入 4.918 V/1.397 A
  （6.87 W）、ICL 1.5 A、失败服务 0、RTC/trace 无残留；快充仍未修复。
  新工具 9 项定向及全脚本 443 项测试通过（含 4 项未提交主摄测试）；原始查询、过滤器、
  各实例 8 核零丢失统计见 `work/fuxi-ucsi-pdos-20261118/`，末查见
  `work/charging-ucsi-final-20261118.log`。没有安装内核、重启或挂起。
  后续单次 `--target local-sink` 查询返回有效 4 字节 `0x2601912c`：固件只暴露本机
  5 V/3 A 固定受电 PDO，没有 9 V/PPS；这不证明该列表完整描述内部选压策略，不能据此
  改写 sink PDO。原厂 `usb_psy_set_prop()` 只处理 ICL，`fastchg_mode` 为只读，未找到
  已证实可修正的高压请求缺陷。新工具 12 项定向测试通过；证据为
  `work/fuxi-ucsi-sink-20261118/`，恢复状态见 `work/charging-sink-final-20261118.log`。
  末查电量由本轮开始的 28% 升到 32%、24.7°C，USB 4.935 V/1.397 A，能持续补电但
  仍非快充验收；failed=0，RTC/trace 清理完成，bootcount 无待确认启动。
  最新 `--contract` 单次查询在源 PDO 读取前后均返回有效 9 字节状态：PD sink，
  RDO `0x130320c8`，请求第 1 档 5 V/2 A，capability-mismatch=0；这只是固件报告，
  不是线上 PD 抓包或实测电流。五个 GET、七实例八核共 840 项丢失字段全部为零；
  工具 19 项定向测试通过。证据为 `work/fuxi-ucsi-contract-20261118/`。
  本轮电量 35%→40%、末查 25.8°C，USB 实测 4.931 V/1.397 A、ICL 1.5 A；
  failed=0，RTC/trace 无残留，蓝牙关闭、自动挂起 nothing，未做控制写入/重启/挂起。
  原厂亮灭屏回调只改变查询间隔，通知启用报文与当前实现一致，未发现漏发的选压命令。
  有界收尾核对旧日志：`scp_inov_conf_for_adpt` 路径投票 1500 mA 并请求 5 V/2 A；
  VDM 请求已到达 PD PHY，随后 Message Failed、DiscModes 超时、TxNOK，不能归咎于
  主机未发请求。对应固件分支实现和可验证的正常策略目标值缺失，未找到安全修复点。
  按用户停止条件结束排查，快充未解决，不新增控制写入、内核改动或诊断实验。
  11-18 本轮发布准备只读快照 92%、Charging、USB 5.065 V/353 mA、current_max 500 mA、
  input_current_limit 100 mA，电池 current_now -44 mA（瞬时值，不能单独作净充电结论）；
  未改充电设置。`rc3` 运行/默认、`suspend6` fallback、bootcount 已确认，蓝牙和自动挂起保持关闭。
  末查证据：`work/charging-bounded-final-20261118.log`。
  11-17 SDP 约 1.8 W、净放电的结果保留为历史，不再当成实时状态。
- 验证：GNSS 定向 22 项、全脚本 443 项通过（含未提交主摄生成器 4 项）；挂起工具 11 项
  覆盖请求入队、迟到/完整信号、超时保留 RTC、包含睡眠的期限、未预选 IRQ/丢失计数、异常
  清理及平台统计缺失/权限失败，禁止触碰会发送 QMP 的 `ddr_stats`；新增只读 `ddr` 和
  cmd-db 快照实机读取无错误，不触发挂起且 RTC/trace 未变；新增 glink 事件缺失拒绝/清理
  及基线在 RTC 之前的检查。离线审计 18 项，覆盖阶段边界、BCM 解码、丢失/缺失证据、
  拒绝多周期、glink 通道/方向及 PMIC 头/传输返回统计；短头不解码，不推断唤醒原因。
  新增 QRTR 缺事件清理、候选/非候选头、长度/控制端口不一致及损坏事件检查。
  最新脚本日志为 `work/charging-ucsi-script-tests-20261118.log`；最近挂起实机采集/审计结果在
  `work/fuxi-suspend-pmic-20261117/`、`work/suspend6-offline-audit-20261117.json`。
  MM 测试编译真实 watcher 和授权/清理回调，覆盖 10 个异步生命周期场景；来源/依赖检查、原生编译及打包通过。
  实机证据在 `work/gnss-session/`、`work/whole-device-*-20261116.*`、`work/mm-resume-*-20261116.log`，不提交原始日志。
  内核诊断开启/关闭及 PDC 模块/禁用配置的相关对象 ARM64 W=1、完整 deb 构建、包载荷/
  来源及配方树重放通过；新补丁 checkpatch 0 错误、1 项新增头文件提醒，未变更 DTB。
  IPCC 内建/模块/禁用和诊断关闭配置 ARM64 W=1、完整 suspend5 deb、Image/config/DTB
  载荷核对和四补丁配方重放通过；新补丁 checkpatch 0 错误、1 项新增头文件提醒。
  PMIC 对象开启/关闭事件追踪 ARM64 W=1、完整 suspend6 deb、Image/config/DTB 核对和
  五补丁配方重放通过；新补丁 checkpatch 0 错误、1 项新增头文件提醒。suspend6 已正常
  启动，bootcount pending=0、deb9 恢复文件校验通过，临时 kernel.conf 已清理。
  QRTR 开启/关闭事件追踪 ARM64 W=1、完整 suspend7 deb、Image/config/DTB 载荷核对和
  六补丁重放通过；新补丁 checkpatch 0 错误、0 警告；该实验内核仍未部署。
  日志为 `work/suspend7-{build,arm64-w1,tracing-off-w1,checkpatch}-20261117.log`，供电证据
  为 `work/suspend7-power-{window,charge-counter}-20261117.jsonl`。
  最新诊断证据在 `work/fuxi-suspend-pmic-20261117/`、`work/suspend6-*-20261117.log`；
  恢复后蜂窝接口 HTTPS=200、WiFi HTTPS 和六类传感器采样通过，关键服务 PID 未变、NRestarts=0。
  上游 `v7.4-rc3` 标准配方重建：归档 SHA256 与上游标签根树核对，14 个项目补丁隔离重放；
  ARM64 deb、Image/config/DTB 载荷和 SHA256SUMS 校验通过。手机包哈希匹配，安装时旧
  `suspend6` 自动保留为 fallback；正常启动已确认，systemd failed=0。新内核现场检查中
  Phosh、触摸、WiFi 证书校验 HTTPS、CPU 调频、热区和六类传感器通过；唯一健康失败为用户
  关闭的蓝牙。启动日志有 IPA、Ath12k、蓝牙及音频固件缺失提示，WiFi 实际可用；仍需后续
  长稳、反复重启及音频/蜂窝等完整回归。设备证据：
  `work/upstream74rc3-{install,reboot-request,boot-health,health}-20261118.log`。
  完整晋级条件未完成；本版本是已发布的 GitHub prerelease，仍不是稳定版或正式签名发行物。
  技术细节见 [GNSS 验收](docs/GNSS按需会话验收-20261108.md)和
  [挂起证据](docs/驱动初始化与功耗验收-20261108.md#普通挂起与恢复检查)。

### 整机下一步

1. 当前 Phosh 已仅保留原生亮度控制；后续使用实际滑块验证偏移和明暗响应、熄屏/亮屏，
   不用直接写 sysfs 或临时背光目录替代原生交互验收，也不套用自定义服务的 60 秒语义。
   传感器轴向/标定和自动旋转仍待现场验证。
2. 充电有界任务已结束：当前可以净充电，快充未修复。缺少 RP_SHORT_VBUS/ICM 固件
   分支及 PD PHY 发送失败的可验证原因，不能从 1.5 A 投票或 TxNOK 推导可安全写入的
   修复值。停止后不追加查询、认证、角色、限流或选压实验，不自动部署其他候选。
   挂起仍为后续待办，不在本轮执行：稳定净充电、电量至少 20%，再安装已构建的
   suspend7，校验默认/恢复入口、正常启动及健康，再做一次有界
   20 秒 RTC + QRTR/PMIC 元数据实验。假设是识别恢复侧 MPSS/IPCRTR 的包类型和候选消息
   编号，停止条件是完成一次采集或出现异常；不凭消息头断言业务身份或最早唤醒源。
   suspend6 不伴随 220 字节 PMIC RX，不能把此前 LPASS/PMIC 关联推广为共同原因。
   同时核实 AOSD/CXSD/DDR 统计有效性。现有 battmgr `diag` 读取会发请求，不作为被动统计。
   Linux 零 BCM sleep 投票不排除硬件阻塞，L2 提示仍在。
   跟进 MM registered 与 connected bearer 不一致。
   不重复相同挂起、不关闭基带或屏蔽 IRQ。
   每个新候选保留准确来源及恢复入口；MM 还需多轮稳定性验证，自动挂起保持关闭，不开始
   续航验收。
3. MM `.2101`、GeoClue `.2301` 已部署；最近自然状态核对确认 GPS_RAW 能力与 LOC 节点可用，未见 LOC 分配警告。
   桌面位置开关当前 false，关闭时真实客户端拒绝路径通过；保留用户设置，定位开启后的授权、室外有效 fix 和误差/UTC、
   功耗及恢复仍待验收。耳机验证 A2DP，操作前核对蓝牙当前用户设置。
4. 核对 CS35L41 保护固件/标定与安全音量；识别 NFC 控制器和接线后实现驱动通路。

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
| 应用 | DSH 续接与设备迁移 |
| 整机 | GNSS 有效 fix、传感器轴向、自动亮度/旋转、A2DP、扬声器保护、挂起/续航、NFC |
| 发布 | 适用的许可、签名、来源与维护审核；不阻塞本地实验 |

完整功能边界见[覆盖核查](docs/功能覆盖核查-20261108.md)和
[相机矩阵](docs/相机功能完成矩阵.md)。历史经过通过 Git 历史及已有报告查阅，
不继续向本文件追加长篇成果清单或复制日期交接。
