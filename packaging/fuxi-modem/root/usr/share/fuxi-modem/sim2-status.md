# SIM2 只读状态 v1

桌面宿主从本机活动 `fuxi` 会话按固定 argv 执行 `/usr/bin/pkexec --disable-internal-agent /usr/sbin/fuxi-simctl sim2-status`。沿用 `org.fuxi.simctl.run` 的现有授权。请异步调用，在打开状态页、手动刷新或服务恢复时重新发现，同一时间保留一个请求。一次调用打开 QMI proxy，申请临时客户端并在退出前释放。副订阅绑定仅作用于本次 NAS 客户端。

退出 0 时读取 stdout 的单个 JSON 对象，接受 `version == 1` 和 `slot == 2`，忽略未知字段。后续兼容扩展沿用版本号，不兼容变更使用新版本号。包版本用于部署追踪。旧 helper 对未知子命令退出 2。`pkexec` 退出 126 表示取消，127 表示授权或执行失败。其他非零、超时或无效 JSON 均表示此次查询失败，应清除旧快照。

| 字段 | v1 语义 |
| --- | --- |
| `listener_active` | `fuxi-sub2-msg.service` 的 systemd 活动态，查询失败或未知状态为 `null`。 |
| `card_state` | UIM 槽 2 报告 `present`、`absent` 或 `error`。槽缺失或无法确认时为 `unknown`。 |
| `subscription_ready` | 槽 2 在位且 secondary GW 对应应用为 ready 时为 `true`。卡缺失、报错、映射至别处、未预配或已知应用未就绪为 `false`。无法判断为 `null`。 |
| `registered` | 副订阅就绪后，按 NAS 客户端绑定副订阅查询网络注册。已注册为 `true`，明确未注册为 `false`。未查询、查询失败或状态未知为 `null`。 |

桌面可用 `subscription_ready == true && registered == true` 显示副卡就绪并已注册。监听服务活动仅表示服务状态。通话控制、语音路由和短信业务需各自验收。此快照仅输出表中状态、版本号与槽号。
