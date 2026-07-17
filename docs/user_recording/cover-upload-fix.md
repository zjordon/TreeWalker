# 竖封面上传选错 file input：根因分析（含 cover_index 方案订正）

> 本文记录抖音重放「竖封面传到错 input（页面显示横封面图）」的调试。
>
> ⚠️ **第一版分析（cover_index 方案）经 live 调试 + 用户抓取的 HTML 推翻**——它建立在错误前提上
> （把全局 `semi-upload-hidden-input` 当横/竖封面）。本文以**订正后的结论**为准；cover_index 代码
> 已实施但**待回滚**。前序：`upload-accept-fix-retrospective.md`（B 方案）、`upload-record-replay-research.md`。

---

## 问题现象

重放 `douyin_redesign5.json`（B 方案后录制）：视频、横封面日志显示成功，但**竖封面实际没传上去**
（竖封面槽位显示横封面图）。重放日志 step12（shu.png）：

```
upload_file 无指纹，按 accept 解析 file input index=9020
Uploaded 'shu.png' to [INPUT] at index 9020  ✅ new <canvas> preview appeared (count 12→13)
```

页面有 **6 个 `accept=image` 的 file input**，验证逻辑只数「任意新 canvas」，误判通过。

---

## 调试方法（脚本，均留存 `examples/`）

用户停在封面编辑器，CDP 连 debug Chrome 跑只读探针：

- `debug_cover_inputs.py`：JS dump 每个 input 的 accept / xpath / 最近可见祖先宽高比。
- `debug_cover_meta.py`：从 `get_state` 的 `file_inputs_meta` dump class_name/visible/upload_ancestor。
- `debug_cover_area.py`：dump 每个 input 所属上传区文本 + 当前 step。
- `debug_cover_switch_auto.py`：**自动**点「设置竖封面」切 step，对比切换前后封面上传 input。

关键证据来自用户抓取的 `rerun-history/conver-dialog.html`（封面弹框完整 HTML）。

---

## ❌ 第一版分析（cover_index）——已推翻

**原推断**：封面编辑器里两组 `semi-upload-hidden-input`（按 DOM 顺序）就是横/竖封面，用扩展捕获的
1-based DOM 顺序 `cover_index` 区分，replay 选第 N 个 primary。

**为什么错**（用户纠正 + HTML 核实）：那两组 `semi-upload-hidden-input` **不是横/竖封面**：

- `conver-dialog.html` **176-182 行**的 `semi-upload-hidden-input`(+replace) 在 `<div class="list-Ldrppp">`
  （`presetList` 区）= **生成参考图**的输入框，不是封面。
- **1363-1369 行**的 `semi-upload-hidden-input`(+replace) 在 `<div class="container-XzaV9h upload-ZOJTUA">`
  （「上传封面」区）= **封面上传区**（横/竖复用，见下），不是「primary=横、replace=竖」。

cover_index 把**参考图 + 封面**两组 primary 按 backend_id 排序当横/竖 → `cover_index=1` 系统性选到
**参考图 input**，`cover_index=2` 才是封面。比旧版（accept+xpath 随机 fallback）**更确定地错**。

---

## ✅ 订正后的真实结构（live 调试验证）

### 封面编辑器的 3 组 `semi-upload-hidden-input`

`debug_cover_meta.py` + `debug_cover_area.py` dump（当前页 6 个 file input）：

| n | bid | accept | class / 所属区 | 身份 |
|---|---|---|---|---|
| 0 | 9688 | image | `upload-btn-input-UY_qeY` / 文本「点击上传新的视频封面」 | **视频封面**（另一功能） |
| 1 | 9658 | video | `upload-btn-input-UY_qeY` | 视频触发器 |
| 2 | 9730 | image | `semi-upload`（在 `list-Ldrppp`/`presetList`） | **参考图 primary** |
| 3 | 9731 | image | `semi-upload ...-replace` | 参考图 replace |
| 4 | 10044 | image | `semi-upload upload-BvM5FF`（在 `container-XzaV9h`「上传封面」） | **封面上传 primary** ✓ |
| 5 | 10045 | image | `semi-upload upload-BvM5FF ...-replace` | 封面 replace |

### 横/竖封面复用同一个上传 input（核心结论）

`debug_cover_switch_auto.py` 自动点「设置竖封面」切 step，对比切换前后：

```
Phase1(设置横封面) 封面 primary xpath = /body/div[11]/.../div[2]/input
Phase2(设置竖封面) 封面 primary xpath = /body/div[11]/.../div[2]/input   ← 完全相同
```

**切换 step 前后，封面上传 primary input 是同一个。** 上传目标（横/竖）由**当前 step**决定：
- `conver-dialog.html` line 12-13：`<div class="step-dXVbPX step-active-AWDV7U">设置横封面</div>` /
  `设置竖封面`——`step-active-AWDV7U` 标记当前 step。
- line 1188-1225：横/竖各有独立 canvas（`horizontal_coverCanvas` / `vertical_coverCanvas`）。
- line 1402：`<button>设置竖封面</button>`——切到竖 step 的按钮。

**机制**：上传前先点 step 切到「设置横封面」或「设置竖封面」，再上传到封面上传区 input（n=4），
图片进当前 step 对应的 canvas。**upload 本身不分横/竖，靠前置 click 切 step 保证方向。**

### 真因

1. cover_index 系统性选参考图 input（n=2/9730），不是封面上传 input（n=4/10044）。
2. **根本难题**：参考图 input（n=2）和封面上传 input（n=4）在 `file_inputs_meta` 里**完全不可区分**
   ——同 class `semi-upload-hidden-input`、同 `visible=True`、同 `upload_ancestor=True`。靠
   `file_inputs_meta` 无法锁定封面上传 input。
3. **唯一稳定区分信号**：封面上传区的 drag-area 文本「点击上传文件或拖拽文件到这里」（n=4）；
   参考图区（n=2）无此文本，视频封面（n=0）是「点击上传新的视频封面」。该文本在 input 的
   drag-area 祖先里，**但 `file_inputs_meta` 不含祖先文本**，故现架构拿不到。

---

## cover_index 方案现状（已回滚，2026-07-16）

上一轮落地的 cover_index 代码基于错误前提（把全局 `semi-upload-hidden-input` 当横/竖封面），经 live
调试推翻后**已按 C 方案完全回滚**，代码回到 B 方案的 accept+xpath 状态：

| 文件 | 回滚内容 |
|---|---|
| `recording_extension/capture/action-recorder.ts` | `onFileChange` 去掉 cover_index 计算，只发 accept |
| `src/tree_walker/recorder/event_mapper.py` | `map_event` 去掉 cover_index 透传 |
| `src/tree_walker/agent/rerun.py` | `_resolve_file_input_by_accept` 去掉 cover_index 参数 + cover 过滤/clamp，恢复 accept+xpath only；两调用点去参 |
| `tests/test_recorder_event_mapper.py` / `tests/test_rerun_history.py` | 回滚 cover_index 测试 |

全量 1961 passed，npm build 过。**当前代码状态 = B 方案（accept+xpath）**，封面上传 input 解析仍无法
区分参考图/封面（file_inputs_meta 不可区分），重放封面步骤大概率仍选错——这是 C 方案「先观察」
要确认的：看 accept+xpath fallback 实际选到哪个 input、横/竖 step 切换是否可靠，再决定上 A/B。

---

## 修复方案（待定）

共同前置：**回滚 cover_index**（扩展 + map_event + rerun + 测试）。主方案三选一：

- **A（推荐）：序列化加 area_text**。改 `dom.py` `_collect_file_inputs`，给 `FileInputInfo` 加
  `area_text` 字段（封面区 drag-area 文本「点击上传文件或拖拽文件到这里」）。replay 优先选
  area_text 匹配的封面上传 input，排除参考图/视频封面/decoy。最稳、纯数据驱动、符合
  `file_inputs_meta`「锁定正确上传入口」的设计初衷；代价是动核心序列化模块 dom.py。
- **B：replay JS 注入定位**。不改序列化，replay 执行 upload_file 时注入 JS 直接找
  「点击上传文件或拖拽文件到这里」所属 semi-upload 的 primary input，再映射 backendNodeId 上传。
  改动集中在 rerun.py，但绕过既有 index/file_inputs_meta 架构，JS↔backendNodeId 映射较繁。
- **C：仅回滚 cover_index 观察**。退回 accept+xpath，不改区分逻辑。因 file_inputs_meta 无法区分
  封面/参考图，大概率仍选错，但先看实际行为再决定。最保守。

---

## 教训

1. **file input 身份不能靠猜**——`semi-upload-hidden-input` 在封面编辑器有 3 组（参考图/封面/视频
   封面），必须连真机 HTML + live 调试才能定身份。第一版凭 class 名想当然，全错。
2. **`file_inputs_meta` 区分力不足**——它只记 input 自身 class/accept/visible/upload_ancestor，
   无法区分「同 class 不同功能区」（参考图 vs 封面）的 input。需要补充**祖先上下文**（如
   drag-area 文本 / 容器 class 前缀）。
3. **横/竖可能是「同一 input + 状态切换」**，而非「两个 input」。录制-重放遇到多槽位时，先搞清
   是多 input 还是单 input 复用 + tab/step 切换——后者只需锁定那一个 input + 保证前置切换可靠。
4. **xpath 跨会话漂移**依然成立（录制 `div[2]/input` vs live `div[2]/input[1]`），但本例真因不是
   xpath，是 input 身份误判（cover_index 选错对象）。

---

## 待办

1. 回滚 cover_index（扩展/map_event/rerun/测试）。
2. 选 A/B/C 之一实施封面上传 input 的正确锁定。
3. 重录 + 重放验证：封面步骤选到 n=4（封面上传区），横/竖靠前置 step 切换正确落入对应 canvas。
