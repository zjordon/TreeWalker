# 任务：找出对 Circe fleece 最不满意的客户的邮箱

## 步骤

### 1. 进入评论列表页
从 dashboard（`http://localhost:7780/admin/admin/dashboard/`）左侧菜单点击 **Marketing**（`<li id=menu-magento-backend-marketing>` 下的 `<a>Marketing</a>`），展开后点击子菜单 **All Reviews**（`<a>` 可见文本 "All Reviews"）。到达 Reviews 页（`/admin/review/product/index/`），顶部显示 "351 records found"。

### 2. 按产品名过滤
在评论表格的过滤行中，找到 **Product 列的过滤输入框**：`<input type=text id=reviewGrid_filter_name name=name>`（注意不是 SKU 列的 `reviewGrid_filter_sku`）。使用 `input_text(index=…, text="Circe", clear=true)` 输入 "Circe"。

### 3. 执行搜索
点击过滤器上方的 **Search** 按钮（`<button title=Search aria-label=Search>`，位于 New Review / Reset Filter 按钮之间）。页面整页刷新，URL 变为 `/admin/review/product/index/filter/...name=Circe.../`，列表只剩 Circe 产品的评论（如 "Circe Hooded Ice Fleece"，SKU WH12，本例返回 2 条：ID 353 "Bad!" by Hannah Lim，ID 352 "Good but not perfect" by customer）。如需看更多行可 `scroll(amount, down)`。

### 4. 判断"最不满意"并获取邮箱
列表行只显示 Nickname（昵称）而非邮箱。需要点击各条 Circe 评论行右侧的 **Edit** 链接（`<a>Edit</a>`，行 `tr` 的 title 属性为 `/admin/review/product/edit/id/<ID>/`）进入评论详情页，在 Review Details 中查看评分（rating 星级）与 reviewer 的 Email 字段。比较各条评分，评分最低（最不满意）的那条的邮箱即答案。

### 5. 完成
用 `done(text, success)` 返回邮箱地址。