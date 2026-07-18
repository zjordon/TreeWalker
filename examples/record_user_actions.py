"""录制入口：启动后端 HTTP 服务，接收扩展事件，落盘 history.json。

前置：

	1. Chrome 以远程调试端口启动（建议用录制专用 profile，提前登录目标站点）：

	   chrome --remote-debugging-port=9222 --user-data-dir=<录制 profile>

	2. 在该 Chrome 里加载 recording_extension/ 未打包扩展（.output/chrome-mv3）。

运行：

	uv run python examples/record_user_actions.py --out myflow.json

然后点扩展「开始录制」→ 执行操作 → 点「停止录制」。产物落 ``rerun-history/<out>``，
可用 ``load_and_rerun`` 重放。

详见 ``docs/user_recording/README.md``。
"""

import argparse
import sys

from tree_walker.browser.session import BrowserSession
from tree_walker.config import _fetch_ws_url
from tree_walker.recorder.recorder import Recorder
from tree_walker.recorder.server import run_server


def main() -> None:
	parser = argparse.ArgumentParser(description="录制用户操作 → history.json")
	parser.add_argument(
		"--ws-url", default=None,
		help="完整 CDP WebSocket URL（ws://localhost:9222/devtools/browser/<id>）；"
		"不指定则从 --cdp-port 自动发现",
	)
	parser.add_argument("--cdp-host", default="localhost", help="Chrome 远程调试 host")
	parser.add_argument("--cdp-port", type=int, default=9222, help="Chrome 远程调试端口")
	parser.add_argument("--out", default="recorded.json", help="输出文件名（相对 rerun-history/）")
	parser.add_argument("--host", default="127.0.0.1", help="后端 HTTP 监听 host")
	parser.add_argument("--port", type=int, default=8765, help="后端 HTTP 监听端口")
	parser.add_argument("--rerun-dir", default="rerun-history", help="落盘根目录")
	parser.add_argument(
		"--upload-dir", default="",
		help="upload_file 约定目录（空→<rerun-dir>/uploads）；扩展只采文件名，重放前把文件放此",
	)
	args = parser.parse_args()

	# Chrome 的 remote-debugging-port 暴露的是 HTTP 端点，不能直连裸 ws://host:port
	# （会 HTTP 404）。必须先 GET /json/version 拿 webSocketDebuggerUrl 再连。
	ws_url = args.ws_url or _fetch_ws_url(args.cdp_host, args.cdp_port)
	if not ws_url:
		print(
			f"✗ 无法从 http://{args.cdp_host}:{args.cdp_port}/json/version 发现 ws_url。\n"
			f"  Chrome 是否以 --remote-debugging-port={args.cdp_port} 启动？",
			file=sys.stderr,
		)
		sys.exit(1)

	browser = BrowserSession(ws_url=ws_url)
	recorder = Recorder(browser, rerun_history_dir=args.rerun_dir, upload_dir=args.upload_dir)

	print(f"✓ 浏览器 CDP: {ws_url}")
	print(f"录制后端监听 http://{args.host}:{args.port}")
	print(f"输出: {args.rerun_dir}/{args.out}")
	print(f"上传约定目录: {recorder.upload_dir}")
	print("加载扩展后点「开始录制」；Ctrl+C 退出。")

	run_server(recorder, host=args.host, port=args.port, default_out=args.out)


if __name__ == "__main__":
	main()
