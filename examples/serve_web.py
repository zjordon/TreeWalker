"""Example: 启动 TreeWalker web 后端（tw-web 的示例入口，独立于 ``tw-web`` CLI）。

web 后端独立于录制后端（录制迁 TreeForge），操作 ``rerun_history_dir`` 下的历史 JSON，
并承载 live agent 控制台 + 流程库。浏览器开 http://127.0.0.1:8766/ 。

Usage:
    uv run python examples/serve_web.py [--host 127.0.0.1] [--port 8766] [--cdp-port 9223] [--history-dir rerun-history]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tree_walker.web.server import run_server


def main() -> None:
    p = argparse.ArgumentParser(description="TreeWalker web 后端")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument(
        "--cdp-port",
        type=int,
        default=9223,
        help="Chrome 远程调试端口：试跑/开始批量重放连这个端口（默认 9223）",
    )
    p.add_argument(
        "--history-dir",
        default=None,
        help="历史 JSON 根目录（默认 settings.agent.rerun_history_dir）",
    )
    args = p.parse_args()
    # 试跑/开始批量经 _build_agent → load_settings() 读 CDP_PORT 连 Chrome；显式置默认 9223
    os.environ["CDP_PORT"] = str(args.cdp_port)
    print(f"TreeWalker web: http://{args.host}:{args.port}/  (Chrome CDP 端口={args.cdp_port})")
    run_server(host=args.host, port=args.port, history_dir=args.history_dir)


if __name__ == "__main__":
    main()
