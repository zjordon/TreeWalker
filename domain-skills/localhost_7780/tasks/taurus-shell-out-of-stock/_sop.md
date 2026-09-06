# 任务：将 Taurus Elements Shell 设为 Out of Stock

Magento Admin（http://localhost:7780/admin/）。

## 步骤

1. **进入商品列表**：左侧菜单点击 `Catalog`（a，可见文本 "Catalog"）→ 展开后点击 `Products`，进入 `/admin/catalog/product/`。
2. **搜索商品**：在关键词搜索框（`input id=fulltext placeholder=Search by keyword`）中 `input_text` 输入 `Taurus Elements Shell`，然后点击 `button aria-label=Search`（可见文本 "Search"）。若之前有遗留筛选，先点 "Clear all"。
3. **进入父商品编辑页**：结果包含 1 条 Configurable Product（ID 350，SKU MJ09，无价格/库存）+ 多条 Simple Product 变体（-XS-Yellow 等）。点击父商品行的 `a aria-label=Edit Taurus Elements Shell`（该 aria-label 不带尺寸后缀，是区分父商品与变体的关键）。进入 `/admin/catalog/product/edit/id/350/`。
4. **修改 Stock Status**：页面需 `scroll` 下拉约 2-3 屏找到 "Stock Status" 字段。它是 `<select>`，`name=product[quantity_and_stock_status][is_in_stock]`（注意：`id` 是随机生成的，每次加载不同，勿依赖 id）。执行 `select_dropdown(index, "Out of Stock")` 一步完成，无需先点击展开。
5. **保存**：点击右上角 `button id=save-button`（可见文本 "Save"）。保存后页面整体刷新。
6. **验证（可选）**：点击 `button id=back` 返回商品列表（关键词筛选保留），点击行内文本 "Taurus Elements Shell" 或刷新确认父商品库存状态已变为 Out of Stock。完成后 `done`。