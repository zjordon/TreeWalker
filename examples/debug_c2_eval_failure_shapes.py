"""C 轮 evaluate 编译失败分类（issue #185 重开证据诊断脚本）。

日志目录经 C2_LOG_DIR 参数化（默认指向本机 C 轮日志路径，跨机器用环境变量
覆盖——review9 #2：硬编码 Windows 绝对路径 + 反斜杠切分只在本机可跑）。
classify 与生产版 _delimiter_scan 保持一致（review9 #4：分叉会扭曲证据
基础的分类结果——含非字符串区反斜杠成对跳过分支）。
"""
import os
import re
import glob
from collections import Counter

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
    """字符串/注释感知的定界符平衡——与生产版 _delimiter_scan 同语义。"""
    stack = []
    in_str = None
    k = 0
    while k < len(code):
        c = code[k]
        if in_str:
            if c == "\\":
                k += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ('"', "'", "`"):
            in_str = c
        elif c == "\\":
            # 与生产版对齐（review9 #4）：regex 转义（\/ \] \)）非字符串区成对
            # 跳过——防 /https?:\/\//g 的 \/ 与收尾 / 相邻被误判为行注释
            k += 2
            continue
        elif c == "/" and k + 1 < len(code) and code[k + 1] == "/":
            break
        elif c in "([{":
            stack.append(c)
        elif c in ")]}":
            if stack and {"(": ")", "[": "]", "{": "}"}[stack[-1]] == c:
                stack.pop()
            else:
                return f"多余闭合({c})"
        k += 1
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
