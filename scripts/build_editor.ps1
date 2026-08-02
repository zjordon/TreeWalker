# 构建 P4 历史编辑器前端（React+Vite）→ 拷到 history_editor/static/ 供 aiohttp 托管。
# 从仓库根运行（PowerShell 5.1 兼容：路径用相对字符串，避开 Join-Path 多参数）。
# 产物（static/）gitignore，不进 git。clone 后跑一次本脚本生成。
$ErrorActionPreference = "Stop"
$ui = "history_editor_ui"
$static = "src\tree_walker\history_editor\static"

Write-Output "npm install..."
npm install --prefix $ui
if (-not $?) { Write-Error "npm install 失败"; exit 1 }

Write-Output "npm run build..."
npm run build --prefix $ui
if (-not $?) { Write-Error "vite build 失败"; exit 1 }

Write-Output "拷 dist -> $static..."
if (Test-Path $static) { Remove-Item $static -Recurse -Force }
Copy-Item "$ui\dist" $static -Recurse
Write-Output "OK 编辑器前端已构建到 $static"
