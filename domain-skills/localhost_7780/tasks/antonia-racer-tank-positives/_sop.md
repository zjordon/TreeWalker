# Task: 查找客户喜欢 Antonia Racer Tank 的原因（正面评价要点）

目标：在 Magento 后台 Reviews 列表中筛出该产品的全部评论，逐一进入 Edit Review 页读取完整 Review 正文（`textarea id=detail`），再总结客户喜欢的理由。

## 步骤

1. **进入 Reviews 列表**：导航到 `http://localhost:7780/admin/review/product/`（左侧菜单 Marketing → All Reviews）。列表默认显示 351 条，需先筛选。
2. **按产品名筛选**：在网格过滤行中找到 Product 列的输入框 `input id=reviewGrid_filter_name name=name`，`input_text` 输入 `Antonia Racer Tank`，然后点击 `button title=Search`（aria-label=Search）。筛选后仅剩 3 条评论（ID 339 / 338 / 337，均为 Approved，产品列 Antonia Racer Tank，SKU WT08）。
3. **逐条打开评论详情**：对每一行，点击该行 `<tr>`（其 `title` 属性即编辑页 URL，如 `.../review/product/edit/id/339/`）或行尾的 `Edit` 链接，进入 Edit Review 页。
4. **读取完整评论**：在编辑页向下 `scroll`，`textarea id=detail` 内可见完整评论文本（列表页的 Review 列只有截断预览，如 "This is in regular rotation at the gym. Its col..."）。如需确认内容可 `click` 该 textarea 使其文本在 DOM 中完整呈现。
5. **返回列表继续**：点击顶部 `button id=back`（title=Back / 可见文本 "Back"）回到筛选后的列表，重复步骤 3-4 处理其余评论。
6. **输出结论**：汇总所有评论中客户表达的喜欢之处。本例参考结论（来自 3 条评论）：喜欢点集中在——色彩鲜艳、在健身房穿搭好看可爱（"Its colorful and looks kinda cute under my exercise tanks"）；适合低强度运动如瑜伽、款式时尚（"very stylish for yoga or something else low impact"）。注意：3 条评论均为 3-4 星左右的客观评价，正面理由主要是外观/时尚性，而非高强度支撑。
7. 完成后调用 `done(text, success)`。

注意：本任务是“喜欢的原因”，与已有的 `antonia-racer-tank-negatives`（不喜欢的原因）互补；同一批 3 条评论需从正面角度总结。