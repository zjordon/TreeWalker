"""Example: 启动 P4 可视化编辑器后端（阶段②）。

编辑器后端独立于录制后端（录制迁 TreeForge），操作 ``rerun_history_dir`` 下的历史 JSON。
前端 SPA（阶段③）接入后，浏览器开 http://127.0.0.1:8766/ 编辑；当前可用 curl 测试端点。

Usage:
    uv run python examples/serve_history_editor.py [--host 127.0.0.1] [--port 8766] [--history-dir rerun-history]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tree_walker.history_editor.server import run_server


def main() -> None:
    p = argparse.ArgumentParser(description="P4 可视化编辑器后端")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument(
        "--history-dir",
        default=None,
        help="历史 JSON 根目录（默认 settings.agent.rerun_history_dir）",
    )
    args = p.parse_args()
    print(f"编辑器后端: http://{args.host}:{args.port}/history/list")
    print("(前端 SPA 待阶段③接入；当前可用 curl 测试端点，如 curl 'http://.../history/list')")
    run_server(host=args.host, port=args.port, history_dir=args.history_dir)


if __name__ == "__main__":
    main()
