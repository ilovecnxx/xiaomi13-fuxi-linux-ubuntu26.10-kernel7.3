# TASKS（2026-11-07 17:10，相机第 1 步暂停）

**最新交接：`docs/交接-新会话-20261107-1652.md`（B/C/D/E 推进 + deb5；下一步相机）**
上一份：`docs/交接-新会话-20261107-1522.md`；E 调研：`docs/调研-E收尾-20261107.md`；相机：`docs/相机调研.md`
通信线：`docs/交接-新会话-20261106-2325.md`；引导层：`docs/计划-标准化启动-20261107.md`。Telegram chat_id 1699534680：所有进度都要转发。

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核以 deb 独立升级、标准引导、设备支持全部打包。

## 当前状态
- 引导：ABL → recovery_b = uboot7（b78e2462…）→ extlinux → chg3（default）/ deb5（fallback），2 次未确认自动回退。
- chg3 = 收敛树 a5dcbb9cc + 认证接口（89846aa3d）+ 有界 PD 限流 setter（ddd260be1）+ UCSI 查询长度修复（e3ebcdbc2），`~/.cache/fuxi-charge74` 构建。回退 deb5 含默认关闭的语音移植，原语音工作树未改。cmdline loglevel=3。
- 开机花屏 SMMU fault 10→0（已实测）；卡 2 IMS 127 s（81voltd v8）；双卡 IMS 正常。
- 包：fuxi-device 0.20261107.2005，desktop/firmware 0.20261107.1519，fuxi-modem 0.20261107.1619。
- 充电基线（11-07 晚，deb5）：电脑 SDP 约 1.8 W，实测净放电；换墙充后 5 V PD 约 6.9 W，121 秒电荷 +44 mAh、电量 26%→27%。当时未改充电参数；见 `docs/充电验收-20261107.md`。Armbian sheng 的 MiPPS 认证程序和配套 qcom_battmgr 扩展调研见 `docs/快充开源实现调研-20261107.md`。
- 充电推进（约 20:00）：已装 chg2，default=chg2、fallback=deb5、pending=0；电池随机挑战认证实测通过（AUTHENTIC=1）。MiPPS/快充仍未解决；数据角色切换实验超时后退到 USB_FLOAT（约 5 V / 0.92 A），软件复位未恢复，需要重新插拔墙充。fuxi-device 0.20261107.2005 自动服务仅做电池认证，禁自动角色切换。代码、镜像哈希、复测入口见 `docs/快充修复进展-20261107.md`。
- 充电复测（20:11–20:17）：用户重插后恢复约 6.9 W，但 real_type=2（DCP）、PDO2=0、Type-C PD=no。重启 chg2 后约 4.6 W；换原 deb5 对照也约 4.6 W、PD=no，认证服务因无接口未运行。现已恢复 default=deb5 / fallback=deb4，pending=0，电池 60%、净入电 +392 mA、26.1 摄氏度，桌面/基带服务正常。下一步需确认充电头型号与线材，必要时给充电头断电复位；不能归因于认证代码或宣称快充已修复。
- 充电头断电后（20:21 起）：恢复 PD、约 6.9 W；随后安装 chg3，启动与电池认证均通过，pending=0。固定 PDO 可读，含 9 V / 3 A；UVDM 版本查询无完成响应。受限 ICL 接口 0/负值/超过固件上限均被拒绝，1.6/1.8/2 A 请求虽 ACK 成功，实际限流仍 1.5 A，结束已恢复原值。UCSI 18 W 设置返回 EOPNOTSUPP，**快充仍未解决**。保持当前 PD 连接，下一步等充电头型号与 A-C/C-C 线材信息；不要再反复要求插拔或重启。详情见同一进展文档。
- 相机第 1 步（17:10 用户叫停，**未编译内核、未上机**）：MCLK 修法写好 → 补丁 `work/patches/camera-mclk-gdsc-20261107/`
  （收敛树与语音树都已打上、未提交）。相机 DTB 编译通过，并已确认是日常 DTB 的超集（只多开 cci0/cci1/camss + LDO 电压）。
- 子 agent 16:45 起 API 余额不足（403），充值前别派。

## 下一步
当前优先：PD 已恢复，确认充电头型号、功率和 A-C/C-C 线材，继续定位固件 5 V / 1.5 A 策略；不要盲目切数据角色、反复重启，或将基础充电/AUTHENTIC=1 单独记为快充通过。

1. 相机：编 deb6——`FUXI_KTREE=~/.cache/fuxi-voice74 FUXI_KOUT=~/.cache/fuxi-voice74-out FUXI_EXTRA_CFG="-e FUXI_CAMERA_POWER -e I2C_QCOM_CCI -e I2C_CHARDEV -e MEDIA_SUPPORT -e MEDIA_PLATFORM_SUPPORT -e MEDIA_CAMERA_SUPPORT -e VIDEO_DEV -e V4L_PLATFORM_DRIVERS -e VIDEO_QCOM_CAMSS -e VIDEO_OV13B10" scripts/fuxi-build-deb.sh deb6`
   → 装上后 `fuxi-kernel-install add 7.4.0-rc1-fuxi-deb6 /boot/vmlinuz-7.4.0-rc1-fuxi-deb6 /usr/lib/linux-image-7.4.0-rc1-fuxi-deb6/qcom/sm8550-xiaomi-fuxi-camera.dtb`
   → 重启前先问用户 → `scripts/fuxi-camera-probe.sh`（MCLK 是否开起来、i2ctransfer 读 OV13B10 chip ID）。回滚 `fuxi-kernel-install set-default 7.4.0-rc1-fuxi-deb5`。
   然后 OV13B10 端点 → CSIPHY → CAMSS → libcamera soft ISP。
2. E 剩余（defconfig 精简、上游补丁、清 shim1–6/t281）→ 蓝牙/WiFi 功耗 → 挂起测试（用户在旁）→ 语音换 307 adsp（待用户同意）。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- recovery_b 只用 `scripts/fuxi-test-recovery.sh`；回滚 uboot5 → uboot4 → shim7。试验间不刷回基线。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作；删大文件。
- 重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`；严禁远程重启 phosh/denial。
- 不提交 q6voice-*.h、android-voice-*、voice-shmdump*、MM 源码、固件、work/apt、work/modem-bin、work/patches/pending/*。
- 手机号不进文档/提交；GitHub 公开仓库推前扫描；dmesg / >20 KB 文件交子 agent。
