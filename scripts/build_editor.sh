#!/usr/bin/env bash
# 构建 web 前端（React+Vite）→ 拷到 web/static/ 供 aiohttp 托管。
# 从仓库根运行（mac/linux）。产物（static/）gitignore，不进 git。clone 后跑一次本脚本生成。
# Windows 用 scripts/build_editor.ps1。
set -euo pipefail

ui="web_ui"
static="src/tree_walker/web/static"

echo "npm install..."
(cd "$ui" && npm install)

echo "npm run build..."
(cd "$ui" && npm run build)

echo "拷 dist -> $static..."
rm -rf "$static"
cp -r "$ui/dist" "$static"
echo "OK web 前端已构建到 $static"
