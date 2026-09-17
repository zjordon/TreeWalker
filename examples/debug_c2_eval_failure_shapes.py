"""C 轮 evaluate 编译失败分类（issue #185 重开证据诊断，临时脚本）。"""
import re
import glob
from collections import Counter

D = r"D:\dev\git\z_jordon\evals\webarena\results\self_overestimate\C_20260916"

blocks = {}
err_pat = re.compile(r"SyntaxError: (.+)")
code_pat = re.compile(r"Validated code \(after quote fixing\):")
warn_pat = re.compile(r"tree_walker\.tools\.actions: evaluate")

for f in sorted(glob.glob(D + r"\task_*.log")):
    task = f.replace("task_", "").replace(".log", "").split("\\")[-1]
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
    """字符串/注释感知的定界符平衡（粗糙版，足够分类用）。"""
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
