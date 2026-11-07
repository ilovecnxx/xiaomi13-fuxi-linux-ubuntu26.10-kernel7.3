# 当前任务与验收状态

当前请求：内核 CI 收尾交给子代理，主任务推进相机。充电故障排查暂停。
CI 已完成云端完整 deb、候选产物/对应源码校验与同条件计时；本地约 296 秒、Actions 约 847 秒，详见 [`构建对比`](docs/完整内核CI与速度对比-20261108.md)。

## 维护入口

- 构建、安装、回退和清理：[`docs/内核维护与独立升级.md`](docs/内核维护与独立升级.md)。
- 标准源码清单：[`kernel/manifest.json`](kernel/manifest.json)；补丁顺序、哈希与完整树校验是权威来源。
- 配置与补丁维护：[`kernel/README.md`](kernel/README.md)、[`kernel/upstream.md`](kernel/upstream.md)。
- 收尾前的详细历史：[`docs/archive/TASKS-20261107-before-standardization.md`](docs/archive/TASKS-20261107-before-standardization.md)。

## 标准化验收

- 已实现：ABL -> recovery_b U-Boot uboot7 -> extlinux -> 独立内核/DTB/initrd；内核 deb 升级。
- 已实测：initramfs-tools 钩子、默认/回退切换、连续两次未确认后自动回退。
- 本轮整理：固定上游压缩包与校验和、14 组补丁、完整源码树校验、统一标准构建入口、构建元数据。
- 本轮整理：移除 ABL 内建 DTB/早期控制台、旧板级包装，精简配置；保留必要诊断接口。
- 本轮整理：旧 shim 脚本移入 scripts/legacy；相机、语音实验源码保持独立。
- 已验收：从新下载压缩包重建并匹配完整源码树；std2 完整包与实机启动通过；84 项回归、WiFi/传感器健康检查通过。
- 标准化收尾时手机保留 std2、chg3、deb5；本次新增 cam1/cam2/cam3 相机实验包。t281 已移除，主机 shim1-6 已归档。
- 收尾记录：[`docs/标准化收尾验收-20261107.md`](docs/标准化收尾验收-20261107.md)。
- 补丁已按子系统分类并记录作者/提交来源；上游邮件发送、合入和模块化配置属于后续独立任务，不能标为已完成。

## 维护工程补强

- 固定第三方源码和补丁：`packaging/sources/manifest.json`；标准包拒绝无源码记录的手机导出二进制。
- 固定 OCI 构建环境、APT 快照、环境包版本记录；应用权威源码迁到 `apps/`，包内文件以 `packaging/` 为准。
- 内核条目操作加锁、空间/完整性预检、持久事务恢复；禁止同名覆盖；启动确认增加本地健康条件。
- GitHub CI：推送/PR 主机回归及源码重建，每周/手动定向内核、bindings 和 arm64 用户空间构建。
- 完整内核打包：独立手动 `Full Kernel Package` 工作流；空目录、固定 amd64 容器、可调并行度；
  deb、对应源码、配置/来源/环境、校验和与分阶段计时作为候选 artifact 保存 7 天；实际首轮结果另记。
- 内核更新、候选晋级和签名快照发布规则见 [`docs/内核持续维护与发布.md`](docs/内核持续维护与发布.md)。
- 上游草案可重复导出并校验重建树；逐行为拆分、绑定完善、真实作者 DCO、维护者审核与实际提交仍未完成。
- LICENSE.md/THIRD_PARTY.md 明确现有权利边界；`packaging/release-policy.json` 中未获确认的许可和发行身份保持未批准。
- 本节记录实现范围；实际测试、构建、设备部署结果另记验收文档，不自动继承 std2 的硬件验收结论。
- 本轮验收：106 项主机回归、61 项应用测试、完整内核包及支持包重复打包通过；
  新设备包重启自动确认和 WiFi/传感器检查通过，详见 [`维护补强验收`](docs/维护补强验收-20261108.md)。

## 设备基线

- 当前运行与默认：7.4.0-rc1-fuxi-std2，回退 chg3，保留 deb5 及 cam1/cam2/cam3 实验包。
  启动确认 pending=0 tries=0 once=0。
- cam3 采集曾出现 RPMh/ICC 阻塞并影响存储；用户物理恢复安卓后，经无线 ADB 返回 Linux，已恢复 std2 默认。
  根文件系统读写、dpkg 审计及 WiFi/传感器健康检查通过；这不代表相机阻塞已修复。
- fuxi-device 0.20261108.2；已安装 desktop/firmware 0.20261107.1519、modem 0.20261107.1619。
  本轮测试源独立保存在 work/apt-maintenance-20261108 与 work/apt-final-maintenance-20261108；主用旧源未切换。
- 相机 DMA heap 权限规则已纳入设备包；0.20261108.2 经传输校验、安装和重启确认。
- 当前构建包含电池认证接口、有界 PD 限流 setter 和 UCSI 响应长度修复；无 chg4 错误日志通道补丁。

## 外设后续

1. 充电：基础充电、电池认证已实测；小米 MDY-12-EF 67 W + 原装 6A A-C 线仍未稳定快充。
   曾短暂测到 8.825 V / 0.903 A；末次高电量窗口为 5 V、约 4.4-5.4 W，不能用该窗口判断峰值能力。
   ADSP 专用日志显示 5 V 合同、1.5 A 内部投票、VDM TxNOK/DiscModes 超时及 RP_SHORT_VBUS；根因未定。
   见 [`docs/快充修复进展-20261107.md`](docs/快充修复进展-20261107.md)。自动服务仅做电池认证，不切数据角色。
2. 相机：TITAN_TOP/MCLK 修复已上机；IMX800/OV13B10 ID 匹配，前摄未确认，长焦读 ID 后重复超时。
   OV13B10 超广角已接通 CAMSS/libcamera，RAW 与普通用户 RGB 实机出图，2104×1560 RGB 连续 120 帧约 30 fps。
   高速模式短横线、标定和桌面取景未验收；后续全尺寸 RGB 启流触发 RPMh/ICC 阻塞，影响 UFS/调频，启停稳定性是下一优先项。
   标准配方未混入实验，固定补丁见 [`kernel/experiments/camera/`](kernel/experiments/camera/README.md)，结果见 [`相机实机记录`](docs/相机验收-20261108.md)。
3. 通话：VoLTE 来电、短信、双卡注册可用；ADSP TX GRAPH_START 返回 -22，通话仍无声。
4. WiFi/蓝牙功耗、挂起、扬声器保护/标定与蓝牙 A2DP 需要单独验收；当前关闭空闲自动挂起。

## 操作边界

- 禁写 GPT/槽位、Android 和固件分区；xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs 均不写。
- 日常内核更新只安装 Linux deb。确需部署引导层时才用 scripts/fuxi-test-recovery.sh，并按 README 核对镜像与备份。
- Linux 重启使用 systemctl reboot --reboot-argument=recovery；不远程重启 phosh/denial。
- 保留当前运行内核及已验证回退；普通清理不用 --force。shim7 镜像仅存档应急，其兼容修复后启动未验收。
- 用户已授权本轮收尾、提交及同步；提交只包含本轮改动，保留原有未提交相机/语音源码及备份。
- 不提交固件、镜像、私钥、原始设备日志、手机号、q6voice/android-voice/voice-shmdump 文件、MM 源码或 work/patches/pending/。
- 外部消息发送需要用户明确指示；历史文档中的转发要求不构成本轮授权。
