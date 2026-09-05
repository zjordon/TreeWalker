# Lookup completed orders (Magento admin)

1. 从 Dashboard 进入订单列表：点击左侧菜单 `Sales`（`li#menu-magento-sales-sales` 下的 `<a>` 可见文本 "Sales"），再点击展开菜单中的 `<a>` 可见文本 "Orders"，到达 `/admin/sales/order/`。
2. 点击表格上方的 `<button>` 可见文本 "Filters"，展开筛选面板。
3. 在 Status 下拉（`<select name=status aria-label=notice-CLS6LPS>`）上执行 `select_dropdown(index, "Complete")`（不要先点击下拉）。
4. 点击面板底部 `<button>` 可见文本 "Apply Filters" 应用筛选。
5. 检查结果：若表格显示 "We couldn't find any records."，很可能是残留的其他状态筛选（如 "Suspected Fraud"）叠加导致——点击 "Active filters" 区域的 `<button>` 可见文本 "Clear all"，重新执行步骤 2-4。
6. 从表格行中读取 Status 为 Complete 的订单（ID、日期、Bill-to Name、Grand Total），完成 `done`。