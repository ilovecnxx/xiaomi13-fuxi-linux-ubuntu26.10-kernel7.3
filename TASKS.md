# TASKS（2026-11-06 13:40）

**最新交接：`docs/交接-新会话-20261106-1340.md`（USB 线）**
旧交接：`docs/交接-新会话-20261103-1940.md`（语音线）；桌面验收：`docs/验收-20261104-上机.md`；旧版 TASKS：`docs/TASKS.md.before-20261105-2243`

## 目标
小米 13（fuxi）主线 Linux 成为可日用完整手机：数据 → 短信 → 通话（VoLTE + 音频）→ 双卡；桌面保持。

## 当前状态
- 设备：在 Ubuntu，内核 #276，recovery_b 里是 `abl-fuxi-usbc-dp-xm3.img`（SHA `87baea0f`），**不是基线**。
  基线 `abl-fuxi-modem16-epmap.img`（SHA `20ac7794`，#222）。现场 role=host 是临时状态，重启后恢复。
  `fuxi-serial-shell` 已 `disable`（永久，重启不恢复），改为需要时手动 `/usr/local/sbin/fuxi-usb-serial on`。
- USB 线：
  - 实测通过：UCSI；dwc3 切 host 后 xHCI 起 USB2 bus1（480M）和 USB3 bus2（10000M）。
  - **11-06 突破（#274 实测）：OTG 主机模式能工作。** 根因不是 VBUS，而是 initramfs 遗留 `/bin/sh -i`（PID 277）占着 ttyGS0 卡住 drd_work；`kill -9 277` 后 `echo host > role` 立即生效，128 GB SSD（ASMedia 2259XT2，174c:235c）枚举成 `/dev/sdg`（uas，480M）。
  - **ttyGS0 永久修复已完成并实测通过（#276）。** 真根因：initramfs 的 busybox **没编 `pkill`/`pgrep`/`ps`**，原来的 `pkill -KILL -f "sh -i"` 一直静默失败。改成：先 `: > /tmp/do_switch` 挡住后台 loop respawn，再扫 `/proc/<pid>/fd` 找仍指向 `/dev/ttyGS0` 的进程 `kill -KILL`（5 轮 × 0.2 s）。#276 开机后无 `sh -i`、无 ttyGS0 占用，role device→host 一次成功，无需手动 kill。
  - eUSB2 repeater 原厂 host 调参已加进 `phy-qcom-eusb2-repeater.c`（0x50–0x5b 共 12 项，init 时快照、device 模式恢复），随 #276 上机；**U 盘枚举稳定性未复测**（用户选择先交接）。
  - 上次 #274 的 USB2 不稳现象：`uas_eh_host_reset_handler FAILED err -19`、`Device not responding to setup address`、`stat urb: status -71`，sdg 出现后掉线；USB3 SS 从未有设备挂上 bus2（怀疑线材只有 USB2）。
  - XM 属性诊断接口 `qcom_battmgr/diag`（读）+ `xm_set`（写）随 #274 起可用；`xm_set 6/109/111` 只回 ACK，不改变 VBUS/角色。
  - 未测：DP 显示（没有显示器）；被电脑识别、休眠后识别；插盘复测。
  - DP 变体的 DTS、Makefile、构建脚本已提交（413ab4a）。
- 充电线（暂停）：PD 快充仍是 5 V，详见旧版 TASKS 和 `work/patches/battmgr-units-20261105/`。
- UFS G5（搁置）：负载中 `reboot -f` 黑屏卡死；补丁在 `work/patches/pending/`，没提交。
- 蓝牙 120 s 超时、SSC 400 缺失：已在 11-06 修好（serdev 重绑，`fuxi-firmware-devices`）。连带隐患也已消除：hexagonrpcd 不再 `After=multi-user.target`（用 `/etc/systemd/system/hexagonrpcd.service` 整份覆盖，已 daemon-reload 验证排序，重启后生效待观察）。
- 语音线（暂停）：start(RX) 成功；start(TX) 仍 -22，0x43ad 方案已否定。
- 桌面线：开机默认会话实际是 **Phosh**（`phosh.service` enabled），`plasmamobile.service` 是 disabled，所以 `plasmamobile` 显示 inactive——不是故障（11-06 已查清）。Plasma 要用 `fuxi-session plasma` 手动切。待做 Plasma 下日用验收、90 Hz、A2DP；是否把 Plasma 改回开机默认待定（远程切换有黑屏风险）。

## 通信线（11-06 16:00，/goal 完成通信所有功能）
卡槽（11-06 实测，与旧记忆相反）：**卡槽 1 = 电信（CHN-CT，Primary GW，IMS idx 0）**，卡槽 2 = 联通（Secondary GW，IMS idx 1）。MM 只看到卡槽 1。
- 实测通过（15:57）：
  - 数据：bearer 0 ctnet / qmapmux0.0，ping 通；5gnr CHN-CT。
  - IMS：sub0 和 sub1 都注册（`/usr/local/sbin/fuxi-qmi-ims.py 0|1`），81voltd 重启后 22 s 内注册。
  - UI（16:02）：装了 `gnome-calls chatty callaudiod`（apt，91 个包，无卸载，日志 `/root/apt-calls-20261106.log`）。gnome-calls 识别 MM Modem/0 的 Voice 接口（origin 0），callaudiod 在跑，Chatty 起来了（EDS 缺失报错不影响）。
- 已修（已部署到手机）：
  - `scripts/fuxi-uim-provision.{py,service}`：MM 启动前补齐缺失的 GW 预配会话（不写 NV）。已 enable，**下次开机才首次跑**。
  - WWAN 射频原来是关的 → `nmcli radio wwan on`；新建 NM 连接 `fuxi-mobile-ct`。
  - 81voltd v6（SIM2 imcn=0 时选错 PDN / P-CSCF）+ v7（`qvd_connection_set_bearer` 里 `mm_bearer_connect` 少一次 `g_object_ref`）。手机二进制 `/usr/local/sbin/81voltd-sub2`（v6 备份 `.v6`），源码 `work/src-untrusted/81voltd-20261101`，补丁导出 `work/patches/third-party-local-20261104/`。
  - 旧 IMS bearer 残留导致 `call-already-present`、SIM1 IMS 丢：`fuxi-ims-bearer-clean` + `81voltd.service.d/30-bearer-clean.conf`（ExecStartPre）。
  - `fuxi-modem-keeper.py`（未改）：开机 300 s 后 sub0 IMS 180 s 未注册就重启射频，最多 2 次。
- 实测通过（11-06 晚，只用运营商免费号）：
  - SIM1 短信发：MM SMS/0 "102"→10001，state sent。SIM1 短信收：回复被 Chatty 自动导入并从 MM 删除，要看 `/home/fuxi/.purple/chatty/db/chatty-history.db` 的 messages 表（python3 sqlite3 只读；手机上没有 sqlite3 命令）。**话费余额 9.83 元**（信用额度 30 元）。
  - SIM1 VoLTE 拨出 10000：3 次，dialing→ringing-out→active 约 2–3 s，挂断正常；MM DEBUG 日志里是 `type='voice-ip'`（走 IMS，5gnr）。**只验证了信令，没有通话音频**。
- 卡槽 2（11-06 16:2x–16:41 实测）：不经过 MM，用 `scripts/fuxi-qmi-sub2.py`（手机 `/usr/local/sbin/`）走原始 QMI，WMS bind 0x004C / VOICE bind 0x0044 sub=1。
  - 短信发：→10010 和用户手机都 sent。短信收：10010 的两段 UCS2 回复已解析出来（transfer-route 的 raw 是裸 TPDU，不带 SMSC）。
  - VoLTE 拨出：→10010 接通；→用户手机 16:41 接通约 20 s（voice-ip）。
- 卡槽 1 →用户手机：16:40 接通约 20 s（MM，active）。用户手机号只存在用户那边，**不写进文档或提交**。
- 来电自动接听测试器（临时，手机上，1 h）：卡2 `systemd-run fuxi-sub2-watch2`（`fuxi-qmi-sub2.py watch 3600 20`，日志 `/root/sub2-watch.log`）；卡1 `fuxi-sim1-answer`（`/root/sim1-answer.sh`，日志 `/root/sim1-answer.log`）。
- **来电与收短信（11-06 16:42，用户真人回拨/回短信，实测通过）**：
  - 卡1 来电：MM Call/5 ringing-in，`mmcli --accept` 成功（`/root/sim1-answer.log`）。
  - 卡1 收短信："测试"被 Chatty 导入（messages 表 direction 1）。
  - 卡2 来电：16:42:29 incoming voice-ip，3 s 后 QMI answer，16:42:32 conversation，hold 20 s 后挂断，end 正常（`/root/sub2-watch.log`）。
  - 卡2 收短信：16:42:27 "测试"（WMS event-report，解析正常）。
  - **注意**：MM 也能看到卡2 的来电（Call/6 ringing-in，但它 accept 会失败："not ringing"）。gnome-calls 会为卡2 来电响铃，可接听按钮无效。以后要么让 MM 过滤 sub1，要么卡2 接听走 `fuxi-qmi-sub2.py`。
- 本机号码查不到：EF_MSISDN 是空的，DMS 返回 NotProvisioned，MM own-numbers 也是空的。所以双卡互拨还没做。
- **重启自动恢复（11-06 实测，两次 `systemctl reboot --reboot-argument=recovery`）**：
  - 第 1 次（16:45）：provision 首次开机运行正常（两个 GW 会话都已存在，无需激活）；约 16:46 数据 + sub0 IMS 起来。**但 16:48:58 heal 服务退出的同一秒 MM 报 qrtr0 no longer controllable 并重探测**（Modem/0→1，数据接口变 qmapmux1.x），内核里没有 modem 崩溃。
  - 根因：qmi-proxy 是被第一个带 PROXY 标志的客户端（heal 脚本里的 fuxi-qmi-ims.py）拉起的，留在 heal 服务的 cgroup 里，heal 一退出就被 systemd 一并杀掉。
  - 修复：`work/rootfs-overlay/etc/systemd/system/fuxi-qmi-proxy.service`（`qmi-proxy --no-exit` 常驻，Before= MM/81voltd/heal/keeper）。
  - 第 2 次（16:5x）：修复后 332 s 时 qmi-proxy 在 `fuxi-qmi-proxy.service` 的 cgroup；heal 正常退出；MM 0 次重探测；Modem/0 connected 5gnr；NM fuxi-mobile-ct 激活；ping 通；sub0、sub1 IMS 都已注册。
- 未测：双卡长时间待机。
- 推测/待查：通话音频仍卡在语音 TX -22（语音线），对方听不到。
- 已提交（6988a2f 及之后）。
- 用户新需求（11-06 17:0x）：双卡要能设置上网卡和拨号卡；开机时开机动画到桌面之间有黑屏，进桌面后要按两次电源键才亮屏。
- **开机黑屏排查（11-06 18:3x 完成，用户确认）**：
  - ✅ 不按键自动亮：`fuxi-display-kick`（`work/rootfs-overlay/usr/local/sbin/` + `etc/systemd/system/fuxi-display-kick.service`，WantedBy=phosh.service）在 phosh 就绪后做一次 PowerSaveMode 3→0。18:08 实测停用它仍要按键，**必须保留**。
  - ✅ 内核 #277 面板冷启动（`panel-xiaomi-fuxi.c` 首次 prepare 先断电 100 ms；原文件 `work/patches/pending/panel-xiaomi-fuxi.c.before-coldstart-20261106`）：0.8 s 起屏幕亮着跑码。用户决定**保留**。
  - ✅ #278 = #277 + initramfs 窗口缩短（`build/initramfs/init`：只有 UDC configured 才留窗口，AUTOBOOT_WAIT 8→3 s、0.2 s 粒度；原文件 `work/patches/pending/initramfs-init.before-autoboot3-20261106`；`fuxi-test-recovery.sh` 串口轮询改 0.2 s）。镜像 `work/kbuild/abl-fuxi-abl-fuxi-autoboot3.img` SHA256 d483a191…，**现在在 recovery_b**。实测：主机 0.4 s 内抓到 stay；systemd 11.24→6.54 s，phosh 16.98→11.93 s，亮屏 ≈20.3→14.9 s；用户确认自己亮、黑屏变短。
  - `fuxi-vt.service` 改为 `WantedBy=plasmamobile.service`（phosh 开机不再提前 chvt 7）。
  - 剩余：跑码→约 3 s 黑（phosh 自身启动）→桌面。可选：splash/quiet 代替跑码。
  - 待用户批准提交：面板驱动补丁、initramfs/init、fuxi-test-recovery.sh/fuxi_serial.py、display-kick 两个文件、fuxi-vt.service。
  - 注意：重启 Linux 必须 `systemctl reboot --reboot-argument=recovery`（普通 reboot 进 Android）；initramfs 放行用 `python3 scripts/fuxi_sh.py --send 'touch /tmp/do_switch; exit'`。**严禁远程重启 phosh.service**。
  - ath12k 0.98 s 的 `-110`：首探无固件，之后正常，不用管。
  - 下一项：双卡上网卡/拨号卡设置（QMI DSD 切换 DDS；默认拨号卡）。

## 下一步（USB 线，细节见交接 §9）
1. 插 U 盘/SSD 复测枚举稳定性（等用户物理操作）：`lsusb -t`、`lsblk`、`dmesg | grep -iE 'uas|scsi|usb [12]-'`；确认 repeater host 调参是否解决 #274 的 EPROTO/掉线。
2. 若挂在 bus1 480M 且仍不稳：查 repeater 实际寄存器；再试原厂 `param-override-seq-host-ms`。
3. 若 USB3 SS 仍无设备：换全功能 USB3 线/换盘；检查 `usb_dp_qmpphy` 当前 mode。
4. 请用户决定基线：采用 #276，还是回滚到 20ac7794。
5. 提交前问用户：`build/initramfs/init`、`phy-qcom-eusb2-repeater.c`、`qcom_battmgr.c` 诊断接口、`hexagonrpcd.service` 覆盖。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- 只用 `scripts/fuxi-test-recovery.sh` 写 recovery_b；试验后回基线（SHA `20ac7794`）。
- **音频镜像必须用 `FUXI_DTB=sm8550-xiaomi-fuxi-r120idle-mic-rupdclk-modem-audio-disp`**。ABL 镜像必须用 `fuxi-build-abl.sh`。
- 不提交：`q6voice-oob.h`、`q6voice-agraph.h`、`android-voice-*`、`voice-shmdump*`、`scripts/fuxi-shmdump3-analyze.py`。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
- 无线 adb 刷机必须设 `ANDROID_SERIAL=<WiFi 端点>`。
