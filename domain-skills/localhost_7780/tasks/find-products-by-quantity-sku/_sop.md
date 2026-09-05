# 按库存数量查产品 SKU（Magento Admin）

目标：找出 Quantity = N（如 10）的产品并读取其 SKU。

## 步骤

1. 若当前不在管理后台，`navigate` 到 `http://localhost:7780/admin/admin/dashboard/`。
2. 在左侧主菜单点击 `Catalog`（`<li id=menu-magento-catalog-catalog>` 内的 `<a>` 可见文本 "Catalog"）。
3. 在展开的子菜单中点击可见文本 "Products" 的 `<a>`，进入 `http://localhost:7780/admin/admin/catalog/product/` 产品列表页。
4. 点击工具栏中的 `Filters` `<button>`（可见文本 "Filters"，与 "Default View"、"Columns" 按钮同行）。筛选面板展开后，DOM 中出现各字段输入框。
5. 在 "Quantity" 字段的两个输入框中填入目标值 N（精确匹配需 from 和 to 都填 N）：
   - `from` 输入框：`<input type=text name=qty[from] maxlength=255>`
   - `to` 输入框：`<input type=text name=qty[to] maxlength=255>`
   用 `input_text(index, "N")` 分别填入。
6. 点击可见文本 "Apply Filters" 的 `<button type=button>` 应用筛选（面板内还有 "Cancel" 按钮，别点错）。
7. 网格通过 AJAX 重新加载。顶部出现 "Active filters: Quantity: N N" 标签，记录数更新。若结果超过一页（分页显示 "of X"），用 `scroll` 浏览或调整每页条数遍历所有行。
8. 逐行读取表格中 "SKU" 列（表头为 `<span>` "SKU"）的 `<div>` 文本，收集所有匹配产品的 SKU。
9. `done` 汇报 SKU 列表。