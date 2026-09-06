# 获取最早完成订单的账单名称（Magento Admin）

**前置**：已在后台 `http://localhost:7780/admin/admin/dashboard/` 登录。

1. **进入订单列表**：左侧菜单先点击可见文本 "Sales" 的 `<a>`（id 形如 `menu-magento-sales-sales` 的菜单项内），展开后点击可见文本 "Orders" 的 `<a>`，或直接 `navigate("http://localhost:7780/admin/sales/order/")`。
2. **打开筛选面板**：在订单列表页点击可见文本 "Filters" 的 `<button>`，展开筛选表单（出现 Purchase Date / Grand Total / Bill-to Name 等输入框）。
3. **筛选状态为 Complete**：对 `name=status` 的下拉（`aria-label=notice-*`，选项含 Canceled/Closed/Complete/…）执行 `select_dropdown(index, "Complete")`。不要先点击下拉。
4. **应用筛选**：点击可见文本 "Apply Filters" 的 `<button>`。页面刷新后 "Active filters: Status: Complete" 出现，记录数从 142 变为完成订单数。
5. **按购买日期升序排序**：点击表头 "Purchase Date" 的 `<th>`（含 `<span>` 文本 Purchase Date）。注意：默认按日期降序（最新在前），点击一次切换为升序，最早的完成订单排到第一行。
6. **读取结果**：排序后第一行数据行的 "Bill-to Name" 列 `<td>` 文本即为答案（录制中该值为 "John Lee"）。无需进入订单详情页。
7. `done("John Lee", true)`。