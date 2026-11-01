# 81voltd 副卡补丁（2026-11-01）

上游 81voltd a7794dd（v1.2.0+2）把所有 IMS 数据请求都交给 ModemManager 的那个 modem（主卡）。基带为卡 2 发的请求（`sub 2, IPv6, apn IMS`）因此被建在主卡上，卡 2 IMS 永远注册不上。

补丁：请求里 subscription > 1 时不走 MM，改为自己开一个 WDS client，`Bind Subscription = <sub>`（libqmi 枚举 1 主 2 副，和请求里的编号一致），然后 Start Network（APN + 请求给的 3GPP profile + IPv6），用 Get Current Settings 取地址回报给基带。subscription 1 的路径不变。去重键加入 subscription。Stop Connection 时 Stop Network 并释放 CID。

- 【已实测】必须先 Bind Mux Data Port（embedded/1/mux 0xF0+sub），否则 Start Network 返回 InvalidOperation(70)；Bind Mux 会把 WDS 客户端的订阅重置回主卡，所以顺序必须是 alloc → mux → Bind Subscription → set IP family → Start（v3，sha256 01a06b13…，已部署，卡 2 PDN 地址为电信段 240e:）。
- 有 APN 时不带 profile（IPv6 + profile 2 会返回 PDN_IPV6_CALL_DISALLOWED 210）。
- 补丁文件 `81voltd-sub2-wds.diff` 已更新到 v3；`.v1` 为旧版。
- 副卡 profile（绑定 sub2 的 WDS）：1 ctnet、2 IMS、3 ctwap、4 sos。
- **v4（2026-11-01 20:34；20:38 已部署到 /usr/local/sbin/81voltd-sub2，v3 备份为 /usr/local/lib/fuxi-81voltd/81voltd-sub2.v3-nocheck；首次启动 a5aa3f0e：25/46/87 s 三次识别 ctnet 并重试，168 s 拿到 IMS PDN，卡 2 registered；228 s MM 段错误后 QMI 连接断开，卡 2 无法恢复，见 `work/claude-logs/sim2-ims-20261101/v4-boot1*.txt`）**
- **v5（2026-11-01 20:55，已编译，未部署）**：qmi-proxy 在 ModemManager 的 cgroup 里，MM 崩溃/重启会把它连同 81voltd 的 QMI 设备一起带走（v4 首次启动 228 s 实测，之后 `CID allocation failed … Broken pipe`）。v5：监听 `device-removed`，丢掉设备和 WDS 状态（`qvd_connection_detach_qmi`），15 s 后在新设备上重连仍被请求的连接；非 QMI 协议错误（断管、超时、CID 分配）10 s 后重开设备；设备打开失败也重试；已关闭的设备在下次请求时重开；重复的 Start Connection 也会重新挂设备；旧客户端迟到的回包被忽略（`wds_stale`）。首次打开副卡 QMI 设备推迟 15 s，避开 MM 建主卡 bearer 的时段（v4 那次主卡报 "No more data format combinations supported"，与副卡同时打开设备有没有关系未证实）。手机构建 `/root/src/81voltd-sub2.v5/build/81voltd`（77460da0…）。v4 补丁留作 `.diff.v4`。
  - 20:57 已部署（v4 备份 `/usr/local/lib/fuxi-81voltd/81voltd-sub2.v4-noreopen`）。首次启动 1c3a57e4：卡 1 33 s registered（没出现 data format 错误）；卡 2 在 40/61/102 s 都拿到 ctnet 并重试；heal 在 125 s 顺序重启（81voltd 也一起重启）；新进程 130 s 收到请求，按计划延迟 15 s 打开设备，146 s 拿到 ctnet，167 s 拿到 IMS PDN，192 s 卡 2 registered，两卡都 registered 并保持到 249 s。**MM 单独崩溃、81voltd 不重启时的 device-removed 重连路径还没在机器上触发过**（`work/claude-logs/sim2-ims-20261101/v5-boot1.txt`）。：连上后 Get Current Settings 多取 APN 名和 IMCN 标志；APN 与请求不同或 IMCN=0 判为错 PDN，Stop Network 后按 20/40/80/120 s 退避重试，客户端保留不重新绑定。原因：开机早期（约 25 s、约 131 s）同样的请求拿到 ctnet 段 240e:469、没有 P-CSCF，卡 2 IMS 不注册；约 428 s 以后拿到 240e:569、APN IMS、IMCN=1（`work/claude-logs/sim2-ims-20261101/pdn-try-2.txt`）。手机构建目录 `/root/src/81voltd-sub2.v4/build/81voltd`（f39e2666…，debugoptimized，与 v3 相同构建类型）。v3 补丁留作 `81voltd-sub2-wds.diff.v3`。

## 部署（手机上）
1. 备份：`cp -a /usr/bin/81voltd /usr/local/lib/fuxi-81voltd/81voltd.orig`
2. 装：`install -m755 build/81voltd /usr/local/sbin/81voltd-sub2`，drop-in `/etc/systemd/system/81voltd.service.d/fuxi-sub2.conf` 改 ExecStart。
3. 重启顺序必须是：`systemctl stop 81voltd; systemctl restart ModemManager; systemctl start 81voltd`（单独重启 81voltd 会让主卡 IMS 掉线不恢复，见 docs/目标-M5.md 11-01 15:11 节）。

## 回滚
删 `fuxi-sub2.conf`，`systemctl daemon-reload`，再按上面顺序重启。系统包 `/usr/bin/81voltd` 不改动。
