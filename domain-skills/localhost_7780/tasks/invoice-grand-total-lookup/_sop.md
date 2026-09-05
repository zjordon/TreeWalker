# 查询发票 Grand Total（以 invoice 000000001 为例）

前提：已在 Magento 管理后台（如 http://localhost:7780/admin/admin/dashboard/）。

1. **进入 Invoices 列表**：点击左侧菜单 `Sales`（`<li id=menu-magento-sales-sales>` 内的 `<a>`，可见文本 "Sales"）展开子菜单，再点击可见文本为 "Invoices" 的 `<a>`。也可直接 `navigate` 到发票列表页。
2. **搜索发票号**：在列表页的 keyword 搜索框 `input#fulltext`（placeholder="Search by keyword"）中输入发票号（如 `000000001`，用 `input_text`），然后点击 `button aria-label=Search`（"Search"）。
3. **打开发票**：在结果表格行中点击发票号单元格（`<td>` 可见文本 "000000001"），进入发票详情页（URL 形如 `/admin/sales/invoice/view/invoice_id/1/`）。
   - 捷径：结果行的 `Grand Total (Base)` / `Grand Total (Purchased)` 列已直接显示金额，无需进入详情页即可回答。
4. **在详情页读取 Grand Total**：向下 `scroll`，在页面底部 "Invoice Totals" 区块中，找到 `<td>` 可见文本 "Grand Total" 所在行，其右侧 `<td>` 中的 `<span>` 即为金额（如 "$36.39"）。
   - 注意区分："Invoice Totals" 下还有 Subtotal / Shipping & Handling / Tax 行，Grand Total 在最上面一行。
5. 用 `done(text, success)` 返回金额，例如："Invoice 000000001 的 grand total 是 $36.39"。