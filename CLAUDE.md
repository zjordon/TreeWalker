# 项目规范

## 代码风格

- **缩进使用制表符（tab）**，不使用空格。编辑文件时务必确保缩进字符与现有代码一致，否则 Edit 工具会因为字符不匹配而失败。

## 运行环境

- **开发环境是 Windows**，命令行工具只有 PowerShell。
- **不要运行 Linux/Unix 专属命令**（如 `ls`、`grep`、`cat`、`tail`、`find`、`rm -rf` 等），必须使用 PowerShell 等价命令或项目内置工具（Read、Grep、Glob、Edit）。
- Bash 工具在 Windows 上使用 MSYS2/Git Bash，部分 Linux 命令可能不可用或行为不同，优先使用 PowerShell 工具。

## 包管理

- **使用 uv 管理 Python 包**，不要使用 pip。安装/更新依赖用 `uv pip install`，同步依赖用 `uv sync`。
- **运行 Python 脚本必须用 `uv run`**，例如 `uv run python examples/xxx.py`、`uv run python -m pytest ...`。直接调用系统 `python` 会因为找不到虚拟环境中的依赖（如 `tree_walker`）而失败。

## 单元测试要求

- **任何代码改动后都必须运行相关单元测试**，确保已有测试全部通过后再结束。
- **新增功能或修改功能时必须同步增加测试用例**，覆盖正常路径和关键边界情况。
- **项目单元测试覆盖率目标 > 85%**，每次提交前检查覆盖率是否达标。
- 测试运行命令：`python -m pytest tests/ -x -v`（同步测试），异步测试需要 `pytest-asyncio`。
