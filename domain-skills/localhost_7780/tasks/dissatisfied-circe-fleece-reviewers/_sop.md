# 查找对 Circe fleece 表达不满的客户（Magento Admin 评论列表）

目标：在 Magento 后台 (http://localhost:7780/admin/) 的 All Reviews 列表中按产品名过滤 "Circe"，然后逐条查看评论详情，识别表达不满的评论者昵称。

## 步骤

1. **进入 All Reviews**：从 Dashboard 侧边栏点击 `Marketing` 菜单项，再点击子菜单 `All Reviews` 链接，进入 `http://localhost:7780/admin/review/product/index/` 的 Reviews 网格（默认显示约 351 条，第一页 20 条）。

2. **清除旧过滤条件**：点击 `Reset Filter` 按钮（`aria-label=Reset Filter`），确保各过滤输入框清空。

3. **按产品名过滤**：在网格第一行过滤栏的 Product 列输入框（`id=reviewGrid_filter_name`, `name=name`, `type=text`）中输入 `Circe`，然后点击 `Search` 按钮（`aria-label=Search`）。结果只剩两条：
   - ID 353："Bad!" — Hannah Lim — "I was really disappointed with the Circe Hooded..."
   - ID 352："Good but not perfect" — customer — "I recently purchased the Circe Hooded Ice Fleec..."
   两者的 Product 列均为 "Circe Hooded Ice Fleece"，SKU WH12。

4. **逐条查看详情**：点击列表行（`<tr title=.../admin/review/product/edit/id/353/>`）或行尾 `Edit` 链接进入 Edit Review 页面。详情页显示 Nickname（如 `value=Hannah Lim`）、Summary of Review（如 `value=Bad!`）、完整 Review 文本（`<textarea id=detail>` 区域，DOM 中文本不显示 value，但页面可见）。通过 Summary/Rating 与评论正文判断是否为不满评论。

5. **返回列表**：点击详情页顶部 `Back` 按钮（`id=back`, `title=Back`）回到过滤后的列表，再查看另一条（ID 352，nickname "customer"，标题 "Good but not perfect"）。

6. **汇总回答**：本例中两条 Circe 评论均表达不满——昵称 **Hannah Lim**（"Bad!"，明确表示 disappointed）和昵称 **customer**（"Good but not perfect"，部分不满）。用 `done(text, success)` 报告客户昵称列表。

注：评论列表中 Review 正文被截断（以 "..." 结尾），完整内容需进入 Edit Review 页面查看。