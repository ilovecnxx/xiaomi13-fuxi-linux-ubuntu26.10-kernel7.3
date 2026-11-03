#!/bin/bash
# 在手机上给 DSH 前端注入 DeepSeek 安卓风皮肤（幂等，改前备份）。
# 用法: apply-theme.sh [前端 dist 目录]
# 无参数时自动在 $(npm root -g) 下查找 @deepseek-ai/dsh-web-frontend/dist。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MARKER="dsh-android-theme"

if [ "$#" -ge 1 ]; then
  DIST="$1"
else
  NPM_ROOT="$(npm root -g 2>/dev/null || true)"
  DIST=""
  if [ -n "$NPM_ROOT" ]; then
    DIST="$(find "$NPM_ROOT" -type d -path '*@deepseek-ai/dsh-web-frontend/dist' 2>/dev/null | head -n1 || true)"
  fi
  if [ -z "$DIST" ]; then
    echo "apply-theme: 找不到 @deepseek-ai/dsh-web-frontend/dist（npm root -g = ${NPM_ROOT:-?}）" >&2
    exit 1
  fi
fi

INDEX="$DIST/index.html"
if [ ! -f "$INDEX" ]; then
  echo "apply-theme: 没有 index.html: $INDEX" >&2
  exit 1
fi

if grep -q "$MARKER" "$INDEX" \
   && grep -q 'deepseek-android.css' "$INDEX" \
   && grep -q 'deepseek-android.js' "$INDEX" \
   && [ -f "$DIST/deepseek-android.css" ] \
   && [ -f "$DIST/deepseek-android.js" ]; then
  echo "apply-theme: 已完整注入，跳过（$INDEX）"
  exit 0
fi

TS="$(date +%Y%m%d-%H%M%S)"
cp -a "$INDEX" "$INDEX.bak.$TS"
echo "apply-theme: 已备份 $INDEX -> $INDEX.bak.$TS"

cp -a "$SELF_DIR/deepseek-android.css" "$DIST/deepseek-android.css"
cp -a "$SELF_DIR/deepseek-android.js" "$DIST/deepseek-android.js"
[ -s "$DIST/deepseek-android.css" ] && [ -s "$DIST/deepseek-android.js" ] || {
  echo "apply-theme: 皮肤文件复制失败" >&2; exit 4; }

python3 - "$INDEX" <<'PYEOF'
import sys, io, os
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
marker = "<!-- dsh-android-theme -->"
if marker in s:
    sys.exit(0)
if "</head>" not in s:
    print("apply-theme: index.html 缺少 </head>，注入失败", file=sys.stderr)
    sys.exit(3)
inj = (
    marker + "\n"
    '    <script src="./deepseek-android.js"></script>\n'
    '    <link rel="stylesheet" href="./deepseek-android.css">\n'
)
out = s.replace("</head>", inj + "</head>", 1)
tmp = p + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(out)
os.replace(tmp, p)  # 原子替换
print("apply-theme: 已注入主题引用")
PYEOF

echo "apply-theme: 完成"
