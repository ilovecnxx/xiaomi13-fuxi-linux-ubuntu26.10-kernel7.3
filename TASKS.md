# TASKS（2026-11-06 00:05）

**完整交接：`docs/交接-新会话-20261103-1940.md`；桌面侧验收：`docs/验收-20261104-上机.md`**

## 充电线（2026-11-05，用户叫停，未完成）
- 设备现状：recovery_b 已回基线 `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`，内核 #222），设备在 Android。
- 原厂数据来源：官方包 `work/stock/fuxi_images_OS3.0.309.0.WMCCNXM_16.0/`（不再从手机上的第三方 307 ROM 取）。
- 已提交：`build/linux` 7412b8bc3 battmgr 单位修正（mAh→µAh、分钟→秒）。
- 【✅ 已修，已提交 `578c1b897`】UCSI 一直 defer 的根因：`&i2c_master_hub_0`（geniqup@9c0000）没开，
  i2c_hub_2 / fsa4480@42 不 probe。改动在 `build/linux` 工作树：
  `sm8550-xiaomi-fuxi-usbc.dtsi` 加 `&i2c_master_hub_0 { status = "okay"; }`、
  新 dts `sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp-usbc.dts` + Makefile 条目、
  `scripts/fuxi-build-abl.sh` 加 `*-audio-disp-usbc` 变体（DISP+AUDIO+USBC，-d DRM_MSM_DP）。
  测试镜像 `work/kbuild/abl-fuxi-ucsi-chg2.img`：UCSI 正常，但充电仍 5 V。
- 【❌ 未解决】PD 快充：ADSP 识别到 33 W PPS（XM APDO_MAX/POWER_MAX=33），但 PD_VERIFED=0、
  QUICK_CHARGE_TYPE=0、CURRENT_STATE=31，一直 5 V / 2 A。用户确认刷官方包同一充电器能快充 → 驱动/用户态问题。
  - 原厂 XM_PROP 下标（0 起）：AUTHENTIC=3、BAP_MATCH=4、VERIFY_PROCESS=12、CURRENT_STATE=21、
    PD_VERIFED=24、BQ2597X_BUS_VOLTAGE=32、QUICK_CHARGE_TYPE=49、APDO_MAX=50、POWER_MAX=51
    （第三方 307 的模块整体偏 4，别混用）。XM opcode GET 0x50 / SET 0x51，owner 0x800A。
  - 假设（按可能性）：
    1. 原厂 `batterysecret` 守护进程在 Linux 上不存在（`/vendor/etc/init/hw/init.batterysecret.rc`，
       boot_completed 启动，监听 usb uevent → 读 real_type/authentic → 写 request_vdm_cmd（XM 13–20）
       做 UVDM 认证 → 写 verify_process（XM 12）、pd_verifed（XM 24））。
       下一步测：0x51 写 XM12=1、XM24=1，看 bq2597x bus 电压是否抬升。
    2. 没有热输入：主线不写 connector_temp（XM 2）和 CHG_CTRL_LIM（BATT 0x31 prop 10），
       原厂 mi_thermald 会写；原厂 thermal-mitigation 表 `<3000000 1500000 1000000 500000>`。
    3. UCSI 下 PD data_role 与 ADSP 期望不一致。
    4. 已排除：notify-enable（主线已发同样消息，厂商驱动无额外 init 消息）。
  - 诊断补丁（XM 读写实验）已撤，留档 `work/patches/battmgr-units-20261105/diag-xm-authentic-experiment.patch`。
- 手机 rootfs 已换成官方包的 ADSP 固件；第三方 ROM 原固件备份在手机 `/root/adsp-thirdparty-bak/`。
- 刷机注意：无线 adb 时必须 `ANDROID_SERIAL=<WiFi 端点>`（`adb mdns services` 查 _adb-tls-connect，
  重启和 `adb root` 后端口会变），留空会默认 USB 序列号 f8286c82 并卡在「先重启回 Android」。

## UFS HS-G5（2026-11-05 复测，仍搁置）
- 补丁 `ufs_qcom_freq_to_gear_speed`：300 MHz 在 HW v5 上映射 G5，在 `build/linux` 工作树未提交（`work/patches/pending/ufs-qcom-sm8550-g5.patch`）。
- 测试镜像 `work/kbuild/abl-fuxi-ufs-g5.img`（#271，SHA `f9056587`）：负载下 HS_GEAR5、约 2.8 GB/s 正常。
- 【❌】负载中 `reboot -f` 第 1 轮黑屏卡死：不是 EDL（USB 无设备），4 分钟不自恢复，需长按电源。已回基线 20ac7794。
- 【❌ 11-05 23:20】关机降档补丁 `ufshcd_shutdown_downshift()`（`ufshcd_wl_shutdown` 里先停 devfreq、
  `ufshcd_devfreq_scale` 到最低频 → HS-G1，失败再 `ufshcd_scale_gear(G1)`，再走 `__ufshcd_wl_suspend`）：
  `build/linux` 工作树未提交，留档 `work/patches/pending/ufs-shutdown-downshift.patch`。
  镜像 `work/kbuild/abl-fuxi-abl-fuxi-ufs-g5-ds.img`（#272，SHA `693b6f92`，G5+降档）：
  负载中 `reboot -f` 第 1 轮 22 s 回 Android，第 2 轮又黑屏卡死（同 #271），长按电源恢复。已回基线 20ac7794。
  没法确认降档那行 `dev_info` 是否执行（pstore 空、journal 不持久）。
- 下一步候选：① 先让关机日志可见（initramfs 串口/ramoops 或 `reboot -f` 前 `echo 8 > printk` + 关机时
  dmesg 写到 /dev/kmsg 以外的地方）再判断降档是否生效；② 降到 HS-G4 而非 G1；③ 关机完全跳过 `ufshcd_suspend`；
  ④ 补测 `systemctl reboot`（负载）和空闲 `reboot -f`（看 G5 一次都没到时是否也挂，排除与 G5 无关）。
  脚本 `work/claude-logs/ufs-g5-reboot/loop.sh f|s|i`，结果 `result.txt`。
- ✅ 11-06 已修：基线 #222 的蓝牙 120 s 超时 + SSC QRTR 400 缺失（从 11-04 16:38 那次开机起出现）是同一个根因。
  蓝牙：initramfs 里没有 qca 固件，0.5 s 时的那次 probe -2，但 `hci_uart_qca` 已经绑定。fuxi-firmware-devices 只 bind
  未绑定的设备，所以 hci0 一直没有固件，fuxi-bt-address 空等 120 s。修法：脚本对 wcn7850-bt serdev 先 unbind 再 bind
  （蓝牙重绑安全，ath12k 仍然禁止重绑）。传感器：上游 hexagonrpcd.service 带 `After=multi-user.target`，而
  fuxi-bt-address 是 `Before=multi-user.target`，所以 hexagonrpcd 被拖到 135 s 才启动，fuxi-wait-ssc 40 s 后先超时，
  sensor_pd 没起来。蓝牙修好后，hexagonrpcd 在 15 s 启动，400 ready，iio-sensor-proxy 正常（11-06 10:55 那次开机验证过）。
  与 11-05 20:18 的 ADSP 固件换官方包无关（失败从 11-04 就开始了）。
  隐患：如果 fuxi-bt-address 以后再超时，传感器也会跟着挂；可以考虑把 hexagonrpcd 的 After=multi-user.target 去掉（未做）。

## 桌面线（当前主线，通信暂停）
- 设备现在跑在 Linux（Plasma Mobile），不是 Android。
- 已装 KDE Plasma Mobile 6.7.5，`plasmamobile.service` 为开机默认会话，中文界面；`fuxi-session plasma|phosh` 切换。
- 已修：Chromium 重启退出 21（清理残留 SingletonLock）、自动旋转方向（mount matrix）、
  Plasma 自动旋转（kwin 未链 QtSensors → `fuxi-autorotate` 调 `kscreen-doctor`，提交 32535d2）。
- 待做：在 Plasma 会话下重跑日用验收（熄屏唤醒、软键盘、Chromium、文件选择器、亮度）；
  90 Hz / 动态刷新（`kscreen-doctor -o` 显示 DSI-1 有 120/90/60 三档）；拔线开机、蓝牙 A2DP。

## 目标
小米 13 主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 通话音频）→ 双卡；桌面功能保持。

## 当前状态（2026-11-04 01:15）
- 【✅ 重大突破】**start(RX) 成功**：删除 RX prepare + STATE(1) + extra=57，多次测试稳定重现
- 【❌ 待解决】start(TX) -22（DSP Error 1，cmd 0x01001002）
- 【❌ 已否定·11-04 实测】TX 端点 miid 0x43af→0x43ad（7 处全改）+ TX EP 参数对齐 Android #934/#936（1 声道、掩码 0x8）：
  两条 TX EP SET_CFG ret=0，但 start(TX) 仍 -22。镜像 `abl-fuxi-voice-tx-43ad.img`（SHA `55f2471f`），
  日志 `work/claude-logs/rtest-20261104-005609/ssh.txt`。改动在 `build/linux` 工作树，未提交。
- 【已恢复】recovery_b = `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`），设备在 Android
- 内核 HEAD：`578c1b897`（11-05 UCSI DT 修复；+ 未提交的 0x43ad/1 声道改动、面板换档改动）；harness HEAD：`7c118f4`

## 下一步
1. 【⚠️ 无结论·11-04 01:07】`voice-tx-43ad` + `voice_replay=1` + extra=57：`amixer cset` 卡住，ssh 120 s 超时，
   dmesg 未抓到（`work/claude-logs/rtest-20261104-010649/ssh.txt`）；systemctl reboot 正常回 Android。
   重测需把 `amixer` 放后台、另起 ssh 抓 dmesg（Android 在 TX OPEN 后、START 前发 OOB SET_CFG #928 和
   `0x01001008` 校准块 #932）。
2. 若 replay 仍失败：从 Android VoLTE TX 图（#928 OOB）提取结构，重新生成 TX 图定义。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。
- ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
