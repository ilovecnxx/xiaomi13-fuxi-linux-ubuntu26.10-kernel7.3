# TASKS（2026-11-07 07:34）

**最新交接：`docs/交接-新会话-20261107-0734.md`（引导层标准化 + 内核独立升级）**
旧版 TASKS：`docs/TASKS.md.before-20261107-0734`（含 Denial 桌面、引导层全记录）、`docs/TASKS.md.before-20261106-2325`（通信线）
通信线交接：`docs/交接-新会话-20261106-2325.md`；引导层详情：`docs/计划-标准化启动-20261107.md`；Denial：`docs/Denial桌面安装-20261107.md`

## 目标
小米 13（fuxi）主线 Linux 成为可日用的完整手机 + 桌面；内核像 PC 一样以 deb 独立升级、标准引导。

## 当前状态
- 引导：ABL → recovery_b = 主线 U-Boot uboot4（b30bfcb3…，自动回退）→ `/boot/extlinux/extlinux.conf` → 内核 + initramfs-tools initrd。
- 默认内核 `7.4.0-rc1-fuxi-deb3`（`scripts/fuxi-build-deb.sh`，树 `~/.cache/fuxi-cons` 分支 `fuxi/consolidate-7.4`），回退 deb2。
- cmdline 在手机 `/etc/kernel/cmdline`（= `scripts/fuxi-boot/kernel-cmdline`），**root=UUID=**（PARTUUID= 会等 30 s）。
- 升级内核：`scripts/fuxi-build-deb.sh <名字>` → 手机 `dpkg -i` → 自动生成 initrd/条目/extlinux/计数；2 次未确认自动回 deb2。
- **设备包（11-07 09:44 重启实测）**：`fuxi-device`（引导工具/服务/udev/UCM，/usr/sbin + /usr/lib/systemd）+ `fuxi-firmware`（592 个固件，来源见 /usr/share/fuxi-firmware/PROVENANCE.txt）
  从本地源 `/srv/fuxi-apt`（`/etc/apt/sources.list.d/fuxi.sources` + `/etc/apt/preferences.d/fuxi`，linux-firmware/generic 内核 pin -1）装。
  主机：`scripts/fuxi-firmware-pull.sh` → `scripts/fuxi-build-pkgs.sh` → rsync work/apt/ → 手机 `apt update && apt install fuxi-device fuxi-firmware`。
  旧手工文件在手机 `/var/backups/fuxi-device-migrate/`；装包前备份 `/root/pre-fuxi-pkg-20261107.tgz`。
- **通信包（11-07 14:26 重启实测）**：`fuxi-modem`（qmi/keeper/simctl/datacard/sub2/uim 脚本 → /usr/sbin；补丁 ModemManager + 81voltd-sub2 → /usr/libexec/fuxi，
  二进制由 `scripts/fuxi-modem-bin-pull.sh` 取到 work/modem-bin，不进 git；MM/81voltd 用 /usr/lib drop-in 切换）。双卡 IMS registered、5G 数据、WiFi 正常。
  旧文件在 `/var/backups/fuxi-modem-migrate/`，装前备份 `/root/pre-fuxi-modem-20261107.tgz`；`/etc/systemd/system/81voltd.service.d/fuxi-debug.conf` 留在 /etc（本地调试）。
  回退到发行版 MM：`systemctl edit ModemManager` 覆盖 ExecStart=/usr/sbin/ModemManager。
- **桌面包（11-07 14:43 重启实测）**：`fuxi-desktop`（denial/plasmamobile/fuxi-autorotate 单元、phosh drop-in、fuxi-session/desktop-app/autorotate、
  /etc/xdg/xdg-desktop-portal/phosh-portals.conf conffile）；默认会话 Denial。/usr/local/share/applications 的软件渲染启动器覆盖已迁走（GPU 已在，renderD128）。
  hexagonrpcd.service 整份覆盖改由 fuxi-device 以 conffile 提供（/etc/systemd/system）。work/rootfs-overlay 在 git 里已经清空。
- **fstab**：根分区 passno=1（备份 /etc/fstab.bak-20261107），initrd 带 e2fsck，启动时由 initramfs 做根分区检查（systemd-fsck-root 因 /run/initramfs/fsck-root 跳过）。
- 桌面：Denial 0.6.2 默认会话（`fuxi-session denial|phosh` 切换）。
- 通信：两卡数据/短信/VoLTE 信令/来电通；**通话音频不通（语音 TX -22）**；卡 2 IMS 待查。

## 下一步（都先问用户）
1. （已完成）U-Boot 现为 uboot7（`work/kbuild/abl-fuxi-uboot7.img`，sha b78e2462f6658c0b，11-07 15:10 刷入 recovery_b）；回滚 uboot5 → uboot4 → shim7。
   菜单「Boot <entry> (booti)」= deb3 + initrd + root=UUID。按音量下进菜单基本按不中（早了 ABL 进 fastboot，晚了 bootdelay=0 直接启动），
   测手动条目改用 `fuxi-bootcount manual-once`（计数块字节 10，U-Boot 先清 0 再走固定条目，cmdline 带 fuxi.boot=once）：实测 1.9 s + 14.1 s。
   注意 root=PARTUUID 会让 initramfs 的 wait-for-root 等满 30 s（uboot6 实测），手动条目不要用 PARTUUID。
2. 回通信线：卡 2 IMS 只读排查（通信交接 §9.1–9.2）、5G 自动回切、移动卡 APN。
3. 功能缺口：挂起默认开、alsa-ucm 通话路由、GPU 加速确认；Denial 退出回 Phosh、basic-check 适配。
4. （已完成，uboot7）U-Boot env 手动 booti 条目改指 deb3/新 DTB 名。
（11-07 15:20 收尾）fuxi-serial-shell.service 改由 fuxi-device 提供（/usr/lib，默认不启用，fuxi-usb-serial on/off）；
   旧 /etc 的 fuxi-remoteproc / fuxi-serial-shell(.d) 移到 /var/backups/fuxi-device-migrate；/etc/systemd/system 已无 fuxi 文件。
   scripts/ 里和包内重复的旧副本（fuxi-boot/、fuxi-qmi-*.py 等）已删，以 packaging/ 为准。手机四个包都是 0.20261107.1519，work/apt 只留最新版。
5. 开机花屏（外观问题，不影响功能）：0.17–0.18 s 有 10 条 arm-smmu context fault，SID 0x1c00（MDSS）读 ABL splash 缓冲 0xB8xxxxxx，
   持续到 0.85 s DPU 接管。修法：DT reserved-memory 的 splash 区加 `iommu-addresses = <&mdss …>` 做直通映射（内核 deb4，要重编 DTB）。
   同一时段的 a740_sqe.fw -2 是 initrd 里没有 GPU 固件，之后从根分区加载成功，无害。

## 红线
- 禁写 GPT/槽位、xbl/abl/tz/hyp、modemst1/2、fsg/fsc、persist、efs；Android 分区只读。
- recovery_b 只用 `scripts/fuxi-test-recovery.sh` 写；回滚 uboot4 → shim7（`work/kbuild/abl-fuxi-*.img`）。试验间不刷回基线。
- 需用户确认：进 Android；git commit/push；刷机/重启；物理操作。
- 重启 Linux 只用 `systemctl reboot --reboot-argument=recovery`；严禁远程重启 phosh/denial 服务。
- 音频镜像 `FUXI_DTB=…-modem-audio-disp`；不提交 q6voice-oob.h、q6voice-agraph.h、android-voice-*、voice-shmdump*、fuxi-shmdump3-analyze.py、MM 源码。
- 无线 adb 刷机设 ANDROID_SERIAL；用户手机号不进文档/提交；initramfs 调试禁 set -x + `$( )` PS4。
