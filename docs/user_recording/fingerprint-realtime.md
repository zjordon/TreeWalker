# 为什么录制侧的 element_hash / stable_hash 必须实时算

> 本文回答一个反复出现的疑问:**录制时为什么不能把事件攒起来、录完再批量算指纹?**
> 配合 [README.md §3.2](README.md#为何实时处理不是-browser-bc-那样批量上传)（架构）与 [troubleshooting.md](troubleshooting.md)（实际踩坑）食用。

---

## 1. 先看哈希到底由什么决定（代码事实）

`stable_hash` / `element_hash` 的算法在 `src/tree_walker/browser/views.py:547-576`：

```
combined = f'{path_str}|{attrs_str}{ax_name}'
hash = sha256(combined)[:16]
```

三个输入：

1. **`path_str`** = `_get_parent_branch_path()`（`views.py:578`）—— 从该节点往上到根的**祖先标签链**（带位置）。代表"元素在 DOM 树里的位置"。
2. **`attrs_str`** = 该元素自己属性里属于 `STATIC_ATTRIBUTES`（`views.py:82`：`class/id/name/type/placeholder/aria-label/title/role`）的那部分；`class` 还会经 `filter_dynamic_classes` 剔除动态 class。
3. **`ax_name`** = `node.ax_node.name` —— **CDP 无障碍树给该节点的可访问性名字**，由浏览器 AX 引擎算出。

而投影 `DOMInteractedElement.load_from_enhanced_dom_tree(node)`（`views.py:747`）取这三个值时，都是从那个**活的 `EnhancedDOMTreeNode`** 上现读的。

---

## 2. 核心认知：哈希是"元素在某一刻的快照"，不是"元素的固有身份证"

三样输入里**没有一样是元素永恒不变的属性**：

- `path_str`（祖先链）—— SPA 里随便一个操作就变：弹个 modal、列表重排、加载态套个 div，祖先链就长一截或挪一位。
- `attrs_str` —— id/name 一般稳，但 `class`、`aria-label`、`placeholder` 会随页面状态变（选中态加 class、按钮文案随状态变 aria-label）。
- `ax_name` —— 最致命：它是**浏览器 AX 引擎在 CDP 调用那一瞬间、根据当前可见文本 / aria-label / 上下文算出来的**。

所以哈希不是 `hash(这个元素)`，而是 `hash(这个元素 + 它此刻的 DOM 上下文 + 此刻的 AX 上下文)`。**它是带时间戳的快照，不是固有属性。**

---

## 3. 为什么"那一刻"必须是事件发生的瞬间

回到抖音录制实际碰到的情况：

**场景 A —— 上传 modal 里的输入框（troubleshooting 问题 3 的 `div[13]`）**：点"上传" → modal 弹出 → 里面的输入框在那一刻有确定的祖先链（`body/div[13]/.../input`）。如果**批量**录，等录制结束才来算哈希 —— 但录制过程中可能又关了 modal、或缩略图出现让 modal 内部多了一层 preview。这时 `get_state` 取到的输入框祖先链已经变了，甚至 modal 关了输入框根本不在 `selector_map` 里。算出来的哈希是"录制结束时的状态"，不是"点击时的状态"。

**场景 B —— Slate 副标题（troubleshooting 问题 6）**：聚焦副标题、敲"browse-use体验…"。Slate 每次敲键都改 span 结构，**`ax_name`（编辑器的可访问性名字）跟着 textContent 变**。如果在用户敲完之后才算哈希，`ax_name` 里已经塞满已输入的文字；但重放时 rerun 走到这一步、要往空字段里输入时，字段是空的 → 重放端算出的 `ax_name` 是空 → **两端 stable_hash 对不上，EXACT/STABLE 匹配直接失败**，只能降级到 xpath/attribute，"全对齐"优势没了。

---

## 4. 致命的技术原因：ax_node 是临时的，根本没法"存下来回头算"

这是比"状态漂移"更硬的理由。

`node.ax_node`（AX 树节点）**只存在于一次 CDP `Accessibility.getFullAXTree` 的实时响应里**（`dom.py:274` 起）。它不在 DOM 快照里，不在任何序列化形态里。整个 `EnhancedDOMTreeNode` 是 `_build_enhanced_dom_tree` 把 **DOM 树 + snapshot + AX 树三源融合**（`dom.py:667`）出来的活对象，**下一次 `get_state()` 或任何导航都会把它整个换掉**（`session.py` 里 navigate / switch_tab / go_back 都清 `_cached_selector_map`）。

所以"先存快照、回头批量算哈希"在工程上**做不到**：

- 存 DOM 快照？ —— 没用，`ax_node` 不在里面，算不出 `ax_name`。
- 存 AX 树响应？ —— 每事件存一份完整 AX 树体积爆炸，还得重跑树融合逻辑（正是 `get_state` 最贵的部分）。
- 唯一能让 `load_from_enhanced_dom_tree(node)` 工作的时机，就是**融合好的活树还在手里的那一刻** —— 即这次 `get_state` 刚返回、还没被下一次覆盖的窗口。

实时管线 `recorder.handle_event` 做的就是：事件来了 → 立刻 `get_state` → 立刻 `locate_by_ref` → 立刻 `load_from_enhanced_dom_tree(node)` 把哈希算出来存进 `AgentHistory`。**没有"先存后算"的空隙**，因为活树不留夜。

---

## 5. 为什么批量会毁掉重放（两端必须同源同时刻）

重放时 `rerun._match_element_index`（`rerun.py:545` 起）走五级匹配，前两级就是比"录制时存的哈希" vs "重放时实时算的哈希"。重放端算哈希的时机是：**rerun 走到第 N 步 → get_state（重建出第 N 步该有的页面）→ 对候选元素算哈希**。

也就是说，**重放端算的是"第 N 步时刻"的哈希**。要让 EXACT/STABLE 命中，录制端存的必须也是"第 N 步时刻"的哈希。而"第 N 步时刻"就是**用户当年操作第 N 步的那一瞬间** —— 唯一一个录制侧和重放侧页面状态会收敛到一致的瞬间。

批量（录制结束才算）取的是"录制结束时刻"的状态，跟重放端要重建的"第 N 步时刻"对不上 → 哈希错位 → 五级匹配前两级全废。

---

## 6. 对照：为什么 Browser-BC 能批量，我们不能

这正是"全对齐"与 BB 的本质区别（README §3.2 那张表）：

- **Browser-BC 根本不算这个哈希**。它每条事件附带一份 DOM 快照，事后用 LLM 离线蒸馏。它不需要"元素跨两个时刻还能对得上"的能力 → 批量无所谓。
- **TreeWalker 的重放命门就是这个哈希**。录制产物要能在"另一个时间、另一个浏览器进程"里被重新定位，就必须在录制时把"那一刻的状态"忠实烧进哈希 → 状态时间敏感 → 必须实时算。

---

## 第二部分：这两级 hash 在重放时怎么"快速且可靠地"找回同一个节点

> 上一部分讲了**录制侧为什么必须实时算**；这部分讲**算出来存着的那个数，重放时是怎么被用来定位的**。同一枚硬币的两面。

### 7. 匹配代码真实怎么用 hash

重放走到某一步，`_match_element_index`（`rerun.py:515`）拿着录制时存的 hash，在**当前** `get_state` 重建出的 `selector_map` 里找：

```python
# Level 1 EXACT（rerun.py:533）
matches = [(idx, e) for idx, e in selector_map.items() if e.element_hash == h_exact]
# Level 2 STABLE（rerun.py:539）
matches = [(idx, e) for idx, e in selector_map.items() if e.compute_stable_hash() == h_stable]
```

要点：**录制侧存的 hash 是个数（sha256 截 64 位），重放侧对当前每个候选节点用同一套算法重算，然后比"数相等"。** 两侧用的是 `compute_stable_hash` / `__hash__` 同一份代码（`views.py:547/566`），所以同一个节点在两侧必然算出同一个数。

### 8. 为什么"编码这三样"就等于"节点身份"

两个 hash 的输入都是 `祖先链 + STATIC_ATTRIBUTES + ax_name`，这三样恰好是一个控件的三维身份：

| 维度 | 回答的问题 | 单看为何不够 |
|---|---|---|
| 祖先链 `path_str` | 它在树的哪个位置 | 树一重排就断 |
| STATIC_ATTRIBUTES | 它自己长什么样（id/name/type/role/…） | 不唯一（同名一堆） |
| ax_name | 它的语义名字 | 不唯一（五个"删除"按钮） |

三维合起来 sha256 成一个数，就把"位置 + 稳定特征 + 语义"压成一个抗碰撞的身份证。单维都不够，合起来才唯一定位 —— 这是"保证找回同一节点"的根据。

### 9. 为什么要两级（EXACT → STABLE）

两级差别**只在 class 的处理**（`views.py:547-576`）：

- `element_hash` = `__hash__`：用**原始 class 字符串**。
- `stable_hash` = `compute_stable_hash`：class 先过 `filter_dynamic_classes` 剔除动态 class（构建哈希后缀、`is-active`/`loading` 等状态类），剔完为空就整个丢掉。

严格 → 宽松的级联：先试 EXACT（class 字节级一致才中，置信度最高）；EXACT 没中（SPA 重放里 class 几乎总漂）→ 试 STABLE 忽略动态 class 再比。代码注释 `Level 2: STABLE（重放首选）` 即此意 —— **重放主力是 STABLE，EXACT 是"能中最好"。**

### 10. "快"到底快在哪

"快"是三层叠加，不是单一 hash table O(1)：

1. **单次比较是整数相等**：两个 64 位 int 比 `==` 近乎一条机器指令；xpath/属性匹配要比字符串、比 dict，贵得多。
2. **一个 key 融合三维**：不用分别比 xpath / 属性 / ax_name 三次结构比较，一次 int `==` 覆盖"位置 + 特征 + 语义"全匹配。
3. **`selector_map` 只含可交互元素**：不是整棵 DOM（几千节点），只收带 `[index]` 的控件（通常几十到一两百），所以扫描本身规模就小。

> `element_hash` 就是 Python 的 `hash(node)`（`__hash__`），节点本就是可哈希的。真要榨性能，`get_state` 后建 `{e.element_hash: idx}` 字典就能把 Level 1 从 O(n) 扫描变 O(1) 查表；当前是 list comprehension 扫描（O(n) 但每次比 int），因 `selector_map` 本来不大，没到需要建索引的程度。

### 11. "保证找到"的真正含义

严格讲不是"保证找到"，而是**一个双向蕴含**：

- **确定性方向（同节点 ⟹ 同 hash）**：目标节点还在、三维身份没变，同一份 sha256 必算出同一个数 → 必然命中。算法决定，不靠运气。
- **抗碰撞方向（同 hash ⟹ 同节点）**：sha256 截 64 位，碰撞概率约 1/1.8e19，实践中等同"是同一节点"。

真正的保证是：**只要目标节点在当前 `selector_map` 里、且三维身份没漂，hash 匹配就一定能捞出它、且不误捞别的。** 这比 xpath（树一变就断）和属性（不唯一）都可靠 —— 录制重放用 hash 而非 xpath 的根本原因。

### 12. 碰撞 / 一模一样的元素怎么办

真正"多个节点 hash 相同"的不是 sha256 碰撞，而是**页面本就有一模一样的控件**（5 个结构/属性/ax_name 全相同的下拉触发器，三维全等 → hash 必等）。这时 `_match_element_index` 用 `_nearest_idx` 兜底：在所有 hash 命中的候选里选**离录制时存的 bounds 中心最近**的那个。hash 区分不开时，位置消歧。

---

## 第三部分：三个输入各自从哪来 —— 为什么 ax_name 决定了架构

> 前两部分讲了"为什么必须实时算"和"重放时怎么用"。这部分回答一个更根本的问题:**为什么把 hash 计算放在后端 CDP,而不是让扩展端自己算?** 答案藏在三个输入的数据来源不对等里。

### 13. path_str 其实只有标签名

`_get_parent_branch_path`（`views.py:586`）的返回是：

```python
return [p.tag_name for p in parents]   # 只有标签名，没有 [n] 兄弟位置
```

即 `path_str` 是 `html/body/div/form/button` 这种**纯标签名链**，不带 `[1]/[2]` 位置（比 xpath 粗）。所以三个输入里它最轻、最易在扩展端复现（走 `parentElement` 收集 tagName 即可）。

### 14. 三输入的数据可得性分解

| 输入 | 来源 | 扩展端能拿到吗 | 复现难度 |
|---|---|---|---|
| `path_str` | 祖先标签名链（纯 tag，无位置） | ✅ content script 走 `parentElement` | 低（但 iframe/shadow 边界要对齐） |
| `attrs_str` | 元素 `STATIC_ATTRIBUTES` + `filter_dynamic_classes` | ✅ `element.attributes` 直接读 | 中（要同步 class 过滤启发式） |
| `ax_name` | CDP `Accessibility.getFullAXTree` | ❌ 必须走 CDP | —— |

**ax_name 是三个里唯一不得不走 CDP 的。** `path_str` 和 `attrs_str` 理论上扩展端都能从原生 DOM 拿到。

### 15. 但"能拿到" ≠ "能复现到字节一致"

hash 是 `sha256(path_str + "|" + attrs_str + ax_name)`，要跨会话匹配，三个字符串必须拼出**一模一样的字节**。扩展端若自己算，得把三套算法都搬到 JS 并永远和 Python 侧同步：

- `path_str`：复刻 TreeWalker 增强树的祖先链语义（iframe 边界停、shadow DOM 怎么算）。简单，但边界情况和 CDP 树未必逐字节一致 —— troubleshooting 问题 3"深嵌套 modal 路径对不上"即是此症。
- `attrs_str`：逐字节复刻 `filter_dynamic_classes` 的动态 class 识别启发式。
- `ax_name`：复刻**浏览器的 Accessible Name 计算规范**（accname）—— `aria-label` / `aria-labelledby`（可指向多 ID）/ 关联 `<label>` / `alt` / 递归 name-from / 隐藏子树 / presentational role…… 在 JS 里重写既庞大又随浏览器版本变（README §2.2："扩展用原生 DOM 只能**近似**算 accessible name，与 CDP 不完全一致 → hash 对不上"）。

### 16. ax_name 是架构的"强制函数"

ax_name **不可避免**要调 CDP。而一旦为它走了 CDP，后端 `_build_enhanced_dom_tree`（`dom.py:667`）顺手就把 **DOM 树 + snapshot + AX 树三源融合**好了 —— 三个输入全都从**同一棵权威树**现取，零移植、零同步、录制侧与重放侧同一段代码。

两条路线对比：

| 路线 | CDP 调用 | 移植成本 | 字节一致性风险 |
|---|---|---|---|
| **A 全对齐（本项目）** | 1 次 get_state | 零 | 零（同一段代码） |
| **B 扩展算 2/3 + 后端只给 ax_name** | 还是 1 次（取整棵 AX 树再按 backendNodeId 匹配，没法"只取一个名字"） | 移植 path_str/attrs_str/filter_dynamic_classes 到 JS 并永久同步 | 高（两套算法漂移 → hash 对不上） |

B 省不掉那次 CDP 调用，却额外背上移植 + 同步成本和字节不一致风险。**ax_name 这个"唯一绑死 CDP"的输入，反过来成了"把全部计算集中在后端"的强制理由** —— 三个里只有它真正离不开 CDP，而正是这一个依赖，决定了整条链路放在后端最划算。

---

## 一句话总结

- **实时算**：哈希是时间敏感的快照（`ax_node` 临时、不可存），唯一能让"录制侧哈希 = 重放侧哈希"的时刻是用户操作那一瞬间 → 必须事件当下、活树还在手里时立刻算。这不是性能取舍，是"算得出 vs 算不出"的硬约束。
- **快速找回**：两级 hash 把"位置 + 稳定特征 + 语义"压成 64 位身份证，录制存、重放同算法重算比 int 相等；EXACT 严格匹、STABLE 剔动态 class 兜底；`selector_map` 只含可交互元素所以扫描小、比 int 又快。同节点三维不变必算出同数 → 这就是"快速且可靠找回"的机制。
- **为何放后端算**：三输入里 `path_str`（纯标签名链）、`attrs_str`（DOM 属性）理论上扩展端都能拿，唯独 `ax_name`（CDP 无障碍名）离不开 CDP。ax_name 逼出一次 CDP 调用 → 索性三个都在后端那棵融合树上算，零移植、零同步、字节天然一致；扩展端算 2/3 反而要背移植成本、还省不掉那次 CDP 调用。
