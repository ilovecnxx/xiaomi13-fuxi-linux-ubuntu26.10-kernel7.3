# TASKS（2026-11-07 17:10，相机第 1 步暂停）

**最新交接：`docs/交接-新会话-20261107-1652.md`（B/C/D/E 推进 + deb5；下一步相机）**
上一份：`docs/交接-新会话-20261107-1522.md`；E 调研：`docs/调研-E收尾-20261107.md`；相机：`docs/相机调研.md`
通信线：`docs/交接-新会话-20261106-2325.md`；引导层：`docs/计划-标准化启动-20261107.md`。Telegram chat_id 1699534680：所有进度都要转发。

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核以 deb 独立升级、标准引导、设备支持全部打包。

## 当前状态
- 引导：ABL → recovery_b = uboot7（b78e2462…）→ extlinux → deb5（default）/ deb4（fallback），2 次未确认自动回退。
- deb5 = 收敛树 a5dcbb9cc（花屏修复）+ 语音移植（未提交，默认关），由 `~/.cache/fuxi-voice74` 构建。cmdline loglevel=3。
- 开机花屏 SMMU fault 10→0（已实测）；卡 2 IMS 127 s（81voltd v8）；双卡 IMS 正常。
- 包：fuxi-device/desktop/firmware 0.20261107.1519，fuxi-modem 0.20261107.1619。
- 充电（11-07 晚，deb5）：电脑 SDP 约 1.8 W，实测净放电；换墙充后 5 V PD 约 6.9 W，121 秒电荷 +44 mAh、电量 26%→27%。未改充电参数；高功率快充未验收，见 `docs/充电验收-20261107.md`。
- 相机第 1 步（17:10 用户叫停，**未编译内核、未上机**）：MCLK 修法写好 → 补丁 `work/patches/camera-mclk-gdsc-20261107/`
  （收敛树与语音树都已打上、未提交）。相机 DTB 编译通过，并已确认是日常 DTB 的超集（只多开 cci0/cci1/camss + LDO 电压）。
- 子 agent 16:45 起 API 余额不足（403），充值前别派。

## 下一步
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
