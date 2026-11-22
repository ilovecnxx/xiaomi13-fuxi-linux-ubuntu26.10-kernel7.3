# 副卡电话与短信 v1

本机桌面宿主使用固定 argv `/usr/bin/pkexec --disable-internal-agent /usr/sbin/fuxi-sim2-telephony <操作>`。授权动作 `org.fuxi.sim2.telephony` 面向 seat0 活动本地 `fuxi` 会话。宿主按用户操作调用，下表的 JSON 输入写入独占管道并关闭写端。号码、正文和收件箱响应仅在调用方内存中处理。

| 操作 | 标准输入 | 返回含义 |
| --- | --- | --- |
| `calls` | 空 | `result=ok` 时 `calls` 含每通的 `id/state/direction`。基带查询省略列表时，常驻监听器可提供 30 秒内收到的呼叫状态，并返回 `source=indication` 和 `observed_boottime_ns`。`calls_unknown` 时清除旧快照。 |
| `wait-calls` | 空 | 后台调用一次。注册成功后最多等 20 秒，首条有效指示返回 `ok` 和 `calls`。`timeout` 表示期间没有收到指示，`calls_unknown` 表示指示内容无法解析，`unavailable` 表示绑定或注册失败。 |
| `dial` | `{"version":1,"number":"…"}` | `result=accepted` 表示调制解调器接受拨号。使用返回的 `call_id` 和后续状态判断是否接通。 |
| `answer`、`end` | `{"version":1,"id":7}` | 根据副订阅快照核对指定 ID。如果基带省略呼叫列表，则使用同一客户端两秒内收到的呼叫指示。`calls_unknown` 表示缺少有效状态，`blocked` 表示该 ID 或状态不适用。`result_unknown` 时重新查询，由用户决定后续操作。 |
| `sms-send` | `{"version":1,"number":"…","text":"…"}` | 完整 JSON 请求最多 4096 个 UTF-8 字节。超过单段容量时自动拼接。读取 `status` 获取提交结果。`segments_total`、`segments_submitted`、`segments_uncertain` 分别统计总段数、已确认数和结果未知数。`submitted` 表示各段获基带确认，`uncertain` 表示当前段缺少确认，`rejected` 表示当前段被拒绝，`partial` 表示已有段确认后在下一段提交前失败。后三种情况均停止发送，重复整条可能产生重复短信。 |
| `sms-inbox` | `{"version":1,"cursor":0,"limit":20}` | 返回 `items/next_cursor/more`。初始游标为 0，后续传回返回值，`limit` 范围 1–50。副卡监听服务保存直接推送的短信，也按新到件通知读取基带指定位置的 3GPP 短信。分段逐段返回，包含拼接元数据。 |
| `sms-message` | `{"version":1,"ids":[0,233]}` | 传入 `sms-inbox` 同组分段的实际记录 ID，可跨页乱序传入。`ok` 返回按段序拼接的 `text` 或 `data_hex`。`incomplete` 返回缺少的序号 `missing`。发送方、拼接引用、编码、应用端口或同序号内容冲突，以及到件跨度超过 24 小时，均返回 `conflict`。 |

所有操作均在一次进程调用内完成。出口为一条版本化 JSON，宿主也应处理 `pkexec` 的授权失败与无输出。单条请求结果未知时，先核对手机或对端状态，重试由用户决定。主卡沿用系统总线 `org.freedesktop.ModemManager1` 的 Voice 和 Messaging 接口，重新发现当前 Modem、Call、SMS 对象路径。通话音频由系统通话路由单独提供。
