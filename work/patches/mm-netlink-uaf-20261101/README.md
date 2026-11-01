# ModemManager 1.25.95 mm-netlink.c use-after-free 修复（2026-11-01）

- 现象：MM 重新探测时段错误（pc=0x30），调用栈 `transaction_complete` ← `netlink_messages_cb`（`work/claude-logs/mm-segv-20261101/gdb-1.txt`）。
- 原因：`transaction_complete()` 先 `g_hash_table_remove()`，表的 destroy 函数 `transaction_free()` 会释放 `tr`，之后又读 `tr->completion_fn`。
- 修复：remove 前把 `completion_fn` 存到局部变量（`mm-netlink-uaf.diff`）。
- 原文件：`mm-netlink.c.orig`（sha256 285d2c9b…）；手机上备份为 `/root/src/modemmanager-1.25.95/src/mm-netlink.c.orig-20261101`。
- 构建：手机上 `ninja -C build src/ModemManager`，产物 build-id fe35ae9f…，sha256 863c42bd…。
- 运行中的版本：`/usr/local/lib/fuxi-mm/src/ModemManager`，build-id c6b20435…（含 mm-simslot 补丁）。构建前的副本在 `/root/ModemManager.c6b20435.build-before-netlinkfix`。
- 上机状态：仅编译通过，未替换（替换和 restart MM 要先问用户）。
