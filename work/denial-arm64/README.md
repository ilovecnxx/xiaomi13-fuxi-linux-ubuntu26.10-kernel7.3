# Denial ARM64 —— fuxi 上的安装记录（2026-11-07 更新）

**结论：现在用上游 v0.6.2 预编译 arm64 deb 安装，不再从源码构建。**
安装脚本：`work/denial-arm64/install-prebuilt.sh`（主机侧运行，详见脚本头部注释）。
实测记录：`docs/Denial桌面安装-20261107.md`。

## 安装产物

- `denial_0.6.2-1_arm64.deb`、`denial-flutter-engine_0.6.2-1_arm64.deb`
- 来源 `https://github.com/denialwm/denial/releases/tag/v0.6.2`（OpenPGP 签名，附 SHA256SUMS）
- 手机侧：`/etc/systemd/system/denial.service`、`/usr/local/bin/fuxi-session`（在
  `work/rootfs-overlay/` 里同一份文件）、`/etc/denial/session.conf` 的
  `DENIAL_SHELL_PROFILE=mobile`

切换会话：

```sh
scripts/fuxi-ssh.sh 'fuxi-session denial'   # Phosh → Denial
scripts/fuxi-ssh.sh 'fuxi-session phosh'    # Denial → Phosh
```

## 旧的源码构建方案（已放弃，仅留档）

2026-10-31 曾尝试在手机上从源码编 Flutter engine + Rust（`scripts/01..07`、
`install.sh`、`uninstall.sh`）。`03-bootstrap.sh` 和 `04-engine.sh` 跑完后，
`phase2-engine.log` 显示 ninja 到 `[648/4525]` 被内核杀掉（rc=137，10 GB 内存
+ 无 swap），`libflutter_engine.so` 没有产出；`/home/fuxi/denial-cache`
（flutter-engine 14 GB、pc-dependencies 1.2 GB）和 `denial-src`（77 MB）留在手机上，
现在不需要了，可清理。

`06-stage-package.sh` / `07-readonly-check.sh` 是按“能在手机上编完”写的打包与检查
脚本，未再使用；它们记录的依赖列表、`DENIAL_SHELL_PROFILE=mobile`、
`denial-session --check` 流程仍有参考价值。
