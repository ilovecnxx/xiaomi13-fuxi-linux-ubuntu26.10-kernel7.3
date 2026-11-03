# 原方案：原生 WebKitGTK 窗口 + DeepSeek 安卓风皮肤（已弃用，保留备份）

改用「原版 UI + 前台 Chromium --app」方案后，本目录文件默认不再启用。
如需恢复原方案：
1. 把 deepseek-android.css / deepseek-android.js 经 apply-theme.sh 注入 dist/index.html
2. 把 dsh-web-window.py 装回 /usr/local/bin/dsh-web-window，dsh-web-open 优先调用它
注意：皮肤类名绑定 dsh-web-frontend 版本，升级 DSH 后可能失配；且注入 dist 会在更新时被覆盖。
