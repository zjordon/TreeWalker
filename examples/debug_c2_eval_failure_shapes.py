"""C 轮 evaluate 编译失败分类（issue #185 重开证据诊断脚本）。

日志目录经 C2_LOG_DIR 参数化（默认指向本机 C 轮日志路径，跨机器用环境变量
覆盖——review9 #2）。classify 直接复用生产版 _delimiter_scan（review10 #1：
复制逻辑必与生产演进分叉——曾缺反斜杠成对跳过与多行注释语义两处）。
"""
import os
import re
import glob
from collections import Counter

from tree_walker.browser.session import _delimiter_scan

D = os.environ.get(
    "C2_LOG_DIR",
    r"D:\dev\git\z_jordon\evals\webarena\results\self_overestimate\C_20260916",
)

blocks = {}
err_pat = re.compile(r"SyntaxError: (.+)")
code_pat = re.compile(r"Validated code \(after quote fixing\):")
warn_pat = re.compile(r"tree_walker\.tools\.actions: evaluate")

for f in sorted(glob.glob(os.path.join(D, "task_*.log"))):
    task = os.path.basename(f).replace("task_", "").replace(".log", "")
    lines = open(f, encoding="utf-8", errors="replace").readlines()
    for i, line in enumerate(lines):
        if warn_pat.search(line) and "failed" in line:
            err = None
            for j in range(i + 1, min(i + 7, len(lines))):
                m = err_pat.search(lines[j])
                if m and err is None:
                    err = m.group(1)[:40]
                if code_pat.search(lines[j]) and j + 1 < len(lines):
                    blocks.setdefault(task, []).append((err, lines[j + 1].rstrip("\n")))
                    break


def classify(code: str) -> str:
    """复用生产版 _delimiter_scan——按构造保证与生产一致（review10 #1）。"""
    stack, extra = _delimiter_scan(code)
    if extra >= 0:
        return f"多余闭合({code[extra]})"
    if stack:
        return f"缺{len(stack)}闭合({''.join(stack)})"
    return "平衡"


total = sum(len(v) for v in blocks.values())
print(f"C 轮 evaluate 编译失败总数: {total}，涉及任务: {len(blocks)}")
shapes = Counter()
for task, items in sorted(blocks.items()):
    for err, code in items:
        cls = classify(code)
        shapes[(err, cls)] += 1
        print(f"  task {task}: err={err!r:46} 定界符: {cls}")
print("\n=== 形态汇总:")
for (err, cls), n in shapes.most_common():
    print(f"  {n}× [{err}] {cls}")
