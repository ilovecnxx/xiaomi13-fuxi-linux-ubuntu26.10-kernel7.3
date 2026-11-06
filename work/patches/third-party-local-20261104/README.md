# 第三方项目的本地改动（2026-11-04 导出）

| 补丁 | 上游 | 基线提交 | 改动 |
|---|---|---|---|
| `81voltd-a7794dd-local.patch` | https://gitlab.postmarketos.org/modem/81voltd | `a7794dd` | 6 files changed, 881 insertions(+), 9 deletions(-)（11-06 更新） |
| `hexagonrpc-23a6964-local.patch` | https://github.com/linux-msm/hexagonrpc | `23a6964` | 2 files changed, 29 insertions(+), 3 deletions(-) |

用法：在对应上游提交上 `git apply <补丁>`。

## 11-06 更新（81voltd）
- `wds_wrong_pdn`：imcn=0 但 APN 匹配时不再判为错 PDN（联通卡 2 IMS 修复）；连接日志打印 P-CSCF 列表，请求参数加 P-CSCF 标志。
- `qvd_connection_set_bearer`：`mm_bearer_connect` 补 `g_object_ref(conn)`（`on_connect` 里会 unref，原来少一次 ref）。
- 手机上对应二进制 `/usr/local/sbin/81voltd-sub2`（v7，源码 `/root/src/81voltd-sub2.v7/`），v6 备份 `81voltd-sub2.v6`。
