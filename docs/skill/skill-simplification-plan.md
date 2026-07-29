# skill 精简重构方案（2026-07-28）

> 基于 model-input（`D:\temp\tree-walker-model-input\bili`）逐条对照当前手写 skill 的分析。
> 触发：A/B 显示 skill 效果不显著（步数噪声、成功率仅踩线达标），诊断 skill 内容太复杂、大量重复 DOM。
> 状态：**方案 + 四文件重写已完成；A/B 验证暂未执行**（待后续单独跑）。

---

## 一、对照结论（关键发现）

1. **冗余 56%**：skill 约 71 条内容，40 条 DOM 已有。`selectors.md` 最严重（77%——整张"稳定标识"列就是把 DOM 的 `placeholder/name/accept/type/contenteditable` 抄一遍，零增量）。
2. **DOM 自带 `[File Inputs]` 块已替 skill 说了大半 quirks**：LLM 看到的 DOM 底部原话就有"Prefer visible…hidden inputs are often decoys…upload reports success but the page does not change"。skill 里"选 visible、hidden 是 decoy、报假成功"几条是 DOM 直译，可删。
3. **skill 描述还不准**：反复说"多个 `name=buploader` 的 file input"，但真正的 decoy `[322]` **根本没有 name 属性**。靠 name 区分是错的。
4. **DOM 自身有冲突该澄清**：`[322]` 顶部 index 对照标 `visible=False`，底部 `[File Inputs]` 块却标 `visible`——两段打架，agent 会困惑。skill 当前没指出，**这才是该补的真坑**。
5. **真正 DOM 看不出、删了会翻车的，全四文件只有约 10 条**。

## 二、新设计原则

**只写 DOM 看不出的 + 补 DOM 的盲区/冲突**，不抄 DOM 属性。四类：
- **动作决策**：用什么动作（`upload_file` 直注 vs 点击——DOM 看不出点击后果和 agent 自身工具能力）
- **时序坑**：跨帧才知道的（标题框封面阶段不在、file input 动态新增、提交后整页跳转）
- **元决策**：怎么判阶段（SPA URL 不变，靠 DOM 内容判阶段）
- **DOM 盲区/冲突澄清**：name 不可靠、visible 标志两段冲突以顶部为准

## 三、精简后的四文件（重写目标内容）

### `_sop.md`（步骤+动作，不抄属性）

```
# B 站视频投稿（member.bilibili.com）

从创作者中心 /platform/home 进入投稿页（/platform/upload/video/frame）。

1. 上传视频：用 upload_file(index, path) 直注，不要点上传按钮（点会弹系统文件框，无法驱动）。等"上传完成"。
2. 上传封面：视频上传后页面新增封面的 file input（accept=image），同样 upload_file 直注。等封面处理完（modal 关闭）。
3. 填信息：标题/分区/标签/简介/创作声明。
   - 标题框在封面阶段不在 DOM，先完成封面再填。
   - 分区两步：先点展开下拉，再点选项。
4. 存草稿：点"存草稿"（不是"立即投稿"）。提交后整页跳转到 success 页。

⚠️ 全流程 URL 不变（/upload/video/frame），判阶段靠 DOM 内容（标题框出现=信息阶段），不靠 URL。
```

### `quirks.md`（真正的坑，约 5 条）

```
# 隐藏坑（DOM 看不出 / DOM 自相矛盾）

1. 上传必须 upload_file 直注，不能点击
   点上传按钮/区域弹 OS 原生文件框，agent 无法驱动。视频和封面都是。
   （DOM 的 [File Inputs] 块会提示"选 visible"，但不会告诉你"必须用 upload_file 工具"。）

2. 多个 file input：靠 visible 标志 + accept 区分，不要靠 name
   视频真身 = visible=True + accept 含 .mp4；有的 decoy 根本没有 name 属性。
   ⚠️ DOM 顶部 index 对照和底部 [File Inputs] 块对同一 input 的 visible 标志可能不一致
   （如 [322] 顶部标 visible=False、底部标 visible）——以顶部 index 对照为准。

3. 标题框时序：封面编辑阶段标题 input 不在 DOM（只有"标题 19/80"文本）。
   不是 bug，先完成封面编辑，切到信息阶段才出现。

4. file input 动态新增：视频上传后页面新增附件(.zip)和封面(image)的 input，
   封面 input 只在封面阶段出现。

5. 提交是整页跳转（非 AJAX）：点存草稿/立即投稿后跳转到 /upload/video/success，
   不要在提交后立刻操作。
```

### `selectors.md`（大幅精简，只留"多候选选哪个"）

```
# 元素选择（仅 DOM 看不出的多候选区分）

元素属性（placeholder/name/accept/tag/可见文本）DOM 里都有，不重复。多候选场景：
- 视频 file input：选 visible=True + accept 含 .mp4 的（详见 quirks#2，不要靠 name）
- 封面 file input：accept=image/png,image/jpeg（封面阶段才出现）
```

### `api.md` → **已删除**（2026-07-28 后续调整）

原计划压成 3 行 URL，但进一步分析发现：起点在 `_sop`、frame URL 在 DOM `[Current URL]`、success URL 在 `quirks#5` 已有——**全部冗余**，故删除整个文件。`SkillLoader` 对缺失文件静默跳过（`if not path.is_file(): continue`），无需改代码。现为三文件结构（`_sop` / `selectors` / `quirks`），渲染后 1793 字符。

> 下方"压成 3 行"是已废弃的中间方案，仅留作记录。

```
# URL
- 起点：/platform/home（创作者中心）
- 投稿全流程：/platform/upload/video/frame（SPA，URL 不变，当前 DOM 可见）
- 提交成功：/platform/upload/video/success（整页跳转，预测值）
```

## 四、关键修正（重写时落实）

1. **删**所有"多个 `name=buploader`"描述——decoy 没有 name，靠 name 区分是错的。
2. **补**"DOM visible 标志两段冲突，以顶部 index 对照为准"——新发现的真坑。
3. **删**和 DOM `[File Inputs]` 块重复的"hidden 是 decoy / 报假成功"。
4. **删**所有 DOM 已有的属性抄录（placeholder/maxlength/accept/tag/可见文本）。

## 五、精简前后对比

| | 重写前（手写版） | 重写后（精简版） |
|---|---|---|
| 总字符（渲染后） | ~7013 | ~1500-1800（约 25%） |
| 冗余率 | 56% | ~0（全为 DOM 看不出的） |
| `selectors.md` | 4.4KB（77% 冗余） | ~0.2KB（只留选择指导） |
| `api.md` | 1.4KB（含空占位/重复） | **已删除**（信息并入 quirks/DOM） |
| 真实信息密度 | 低 | 高 |

## 六、验证计划（暂未执行）

重写后建议 A/B 验证：**精简版 vs 当前手写版 vs baseline**，N≥5/组，看**成功率**（不是步数——前面方法论总结：步数是噪声，成功率才是可靠指标）。

预期假设：精简版若质量更高（信息密度高、不误导），成功率应 ≥ 手写版；若两者持平，说明 skill 内容长短不是瓶颈，瓶颈在任务本身对 LLM 的难度（baseline 已 60-100%，边际空间有限）。

此验证待后续单独执行，本次只完成方案 + 重写。
