# issue #185-c2 实施方案：定界符平衡修复候选 + 截断提示纠偏 + fixer 无损性单测

- 日期：2026-09-18；分支 `fix/185-c2-balance-repair`（自 master `9ffe21c`）
- 依据：`docs/bug-fix/185-c2-reopen-analysis.md`（C 轮 22/22 编译失败全为定界符失衡；「传输截断」零证据；首轮方案 §8 后置的平衡修复候选解除后置）
- 范围：`session.py` 自愈候选扩展 + 提示措辞 + 两个测试文件；**不含** #186-c2 的行为层（另案）

## 0. 重开请求 → 改动映射

| 重开诉求（issue 评论） | 改动 | 层级 |
|---|---|---|
| 同类失败在修复后代码上复现（18/22 缺/多闭合无候选） | **A** 平衡修复候选（补缺失闭合 / 删首个错位闭合 / 裸 return 组合候选） | P0 |
| 「truncated」叙事误导（含我方 hint 措辞助长） | **B** 截断提示改为失衡语义 + 触发面扩展到 token 错误 | P0 |
| 验收建议「regex 字面量/嵌套引号表达式原样到达」 | **C** `_validate_and_fix_javascript` 无损性单测 | P0 |

## 1. A：平衡修复候选（`session.py` `_syntax_repair_candidates` 扩展）

### A0 新增模块级助手（`_syntax_repair_candidates` 同文件）

```python
def _delimiter_scan(code: str) -> tuple[list[str], int]:
    """字符串/模板字面量与 // 注释感知的定界符扫描。

    返回 (未闭合栈——按开序, 首个错位闭合符的下标；无则 -1)。
    已知局限（候选由编译试跑把关，无害）：regex 字面量内的 `[](){}` 会被
    误计入栈——产生的候选若不合法会在重试处被丢弃。
    """
```

实现即诊断脚本（`examples/debug_c2_eval_failure_shapes.py` 的 `classify`）的扫描器下沉：遇 `"'`` ` 进串态（`\\` 跳双字符）；`//` 截断到行尾（保守：`/* */` 与 regex 边界不识别，见局限注）；`([{` 入栈；`)]}` 与栈顶匹配则弹栈、不匹配则记 `first_extra` 并停止。

### A1 候选生成（按错误形态，全部走既有按序试跑通道——编译期错误无副作用）

1. **`Unexpected end of input`（缺闭合，C 轮 13 处）**：
   - 候选①：`code + "".join(close(c) for c in reversed(stack))`——栈序逆置补全。真实样本验证：549 的 `((function(){…})()` 栈尾 `(` → 补 `)` 即合法；550 的缺 3/4 同理（补全后经编译把关）。
   - 候选②（保险，仅当候选①与栈含 `(` 时）：`code + ")"`——最小修补（外层包裹括号漏配的最常见单字符形态）。
2. **`Unexpected token '}'` / `')'` / `']'`（多余闭合，C 轮 5 处）**：
   - 候选①：删除 `first_extra` 处的单个字符。真实样本验证：699 的 `…return 'not found'}})())` 首个错位 `}` 删除即平衡；700 同型。
   - 候选②：删除前两个错位闭合（重扫后再删一个）——应对双错位（703 的多余 `]` 伴 EOF 形态按 EOF 分支处理，此候选兜底）。
3. **`Illegal return statement` 且扫描失衡（698/700 形态——裸 return 叠加失衡，现有 IIFE 包裹候选必败）**：
   - 组合候选：`"(()=>{\n" + code + 补全闭合 + "\n})()"`——内层先补平衡再包 IIFE，return 合法化与括号补全一次到位。仅在现有包裹候选（保持不变，平衡代码的主路径）之后追加。
4. **`Missing catch or finally after try` 且扫描失衡（698 形态）**：
   - 组合候选：现有形态②候选（去尾重建）基础上对内层补全闭合——实现为：`code + 补全闭合` 后再套现有两个 catch 候选逻辑生成一条。

候选总数每形态 ≤2-3，与既有候选共用 `_PARAM_VALIDATION`… 不对——共用 evaluate 内的 for-retry 循环（session.py 既有试跑通道），全败抛原错（携带 B 的新提示）。

### A2 与既有候选的顺序

`_syntax_repair_candidates` 返回列表的顺序即试跑顺序：裸 return / 缺 catch 的**既有候选保持在前**（平衡代码主路径），失衡组合候选追加在后；EOF/token 分支为新增独立分支。纯函数改动，签名不变。

## 2. B：截断提示纠偏（`session.py` 3636 附近）

```python
                if "Unexpected end of input" in err_text:
                    msg += ("\n⚠️ The code looks truncated — split it into shorter "
                            "evaluate calls.")
```

改为（触发面同时扩到 token 闭合错误）：

```python
                if "Unexpected end of input" in err_text or "Unexpected token" in err_text:
                    msg += ("\n⚠️ The code has unbalanced braces/parens (this V8 error "
                            "means delimiters never matched, not that text was cut). "
                            "Check that an IIFE prefix `((function(){...` has its "
                            "matching `}))` / `)())` suffix; keep code under ~300 "
                            "chars or split into several evaluate calls.")
```

同步更新 `tests/test_p7_form_interaction_fixes.py::test_truncation_error_gets_hint`（match="truncated" → match="unbalanced braces/parens"）。

## 3. C：fixer 无损性单测（`tests/test_evaluate.py` 追加）

```python
class TestValidateAndFixJavascriptLossless:
    """issue #185-c2 重开验收：长表达式 / regex 字面量 / 嵌套引号样本经
    _validate_and_fix_javascript 必须**逐字节不变**——C 轮「传输改写」指控的
    无损性正面证明（22/22 失败全为定界符失衡，无一例改写签名）。"""

    def test_long_code_with_regex_and_nested_quotes_untouched(self):
        # >500 字符；单引号串内嵌双引号（无反斜杠转义→不触规则1）；
        # regex /\n+/g、/catal[a-z]+\/p/ 为单反斜杠形式（JSON 解码后的正确
        # 形态→不触规则2/7）
        ...

    def test_rules_still_fire_on_double_escaped_input(self):
        # 反向护栏：\\" 与 \\d 仍按既有规则归一（防把规则误删）
        ...
```

样本直接取 C 轮真实代码形态（544 的 `replace(/\n+/g,' | ')` 长链、549 的嵌套单双引号混排）。

## 4. 实施顺序与提交切分

单 PR 两批（用户授权提交时）：

1. **A0+A1+A2**（扫描器下沉 + 候选扩展）+ 候选单测（真实样本驱动）；
2. **B+C**（提示措辞 + 无损性单测 + 既有断言更新）。

末尾全量 `uv run python -m pytest tests/ -x -v --cov`（>85%）。

## 5. 测试与验收清单

- [ ] 候选单测（`tests/test_p7_form_interaction_fixes.py` 扩展，真实样本驱动）：
  - 549 样本（`((function(){…})()` 缺 1 闭）→ 候选补 `)` 后平衡；
  - 699 样本（首个错位 `}`）→ 删除候选平衡；
  - 698 样本（裸 return + 缺闭合）→ 组合候选（补全+包裹）平衡；
  - 已平衡代码不产生任何平衡类候选（防误改）；
  - regex 含 `[]` 的样本（已知局限）→ 候选允许无效，由编译试跑兜底（mock 断言重试链）。
- [ ] session 级自愈重试：EOF 真实 CDP 形状桩 → 候选重试成功返回值（沿既有 `test_illegal_return_retried_with_iife_and_succeeds` 模式）。
- [ ] hint 新文案断言 + 触发面（token 错误也触发）。
- [ ] 无损性单测两向（样本恒等 + 双重转义仍归一）。
- [ ] 全量回归 + 覆盖率 >85%。
- [ ] （下轮评测复查，非门槛）同类任务 evaluate 编译失败数显著下降、agent 自评不再出现 "truncated" 叙事。

## 6. 风险与不做

| 项 | 评估 |
|---|---|
| 补全/删除候选改变语义 | 候选即「LLM 自以为写对的程序」的最近似形；编译试跑把关 + 语法错误无副作用的既有哲学覆盖；agent 可从结果纠偏 |
| regex 内定界符误扫 | 已知局限，候选无效即被编译试跑丢弃（无害）；不做完整 JS 词法 |
| `/* */` 注释不识别 | 同上局限注记；C 轮 22 样本无一含块注释 |
| 触发面扩展误报（token 错误非失衡） | "Unexpected token" 也可能来自真语法错（如 `var x=;`）——提示无害且候选编译把关 |
| 端到端「原样到达」自动化 | 不做（需真实 LLM 发码，波动大）；C 项无损性单测 + 22/22 分类证据已覆盖关切 |

## 7. 缩进与规范

- `session.py` / 测试文件按各自现状（tab / 4 空格实测）；
- 诊断脚本 `examples/debug_c2_eval_failure_shapes.py` 已在 master（9ffe21c 后未动，随分支可见）；
- 不主动 commit/push（CLAUDE.md）。
