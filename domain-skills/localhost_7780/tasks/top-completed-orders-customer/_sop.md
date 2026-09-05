# 任务：查询历史中完成订单数最多的客户

入口：Magento 后台 `http://localhost:7780/admin/admin/dashboard/`（已登录 admin）。

## 步骤

1. **进入订单列表**：点击左侧菜单 `Sales`（`a` 可见文本 "Sales"），再点击展开的子菜单 `Orders`（`a` 可见文本 "Orders"），到达 `http://localhost:7780/admin/sales/order/`。
2. **清空旧筛选**（如有）：若页面显示 "Active filters"（如残留 Status 筛选），点击按钮 `Remove` 或 `Clear all` 先移除。
3. **打开筛选面板**：点击按钮 `Filters`（可见文本 "Filters"），展开筛选表单。
4. **按状态筛选 Complete**：找到 `Status` 的下拉框（`select name=status`，选项含 Canceled/Closed/Complete/Suspected Fraud 等），直接 `select_dropdown(index, "Complete")`，无需先点击打开。
5. **应用筛选**：点击按钮 `Apply Filters`。页面刷新后显示 "Active filters: Status: Complete"，并显示 "153 records found"（数量随数据变化）。
6. **统计各客户订单数**：结果默认按 Bill-to Name 分组排序（同一客户相邻），表格每行一个订单，客户名在 "Bill-to Name" 列。逐屏 `scroll(amount, direction)` 读完整个列表（每页约 20 行，用分页控件翻页），按客户名累计行数。
7. **得出结论并回答**：本次记录中，完成订单最多的客户是 **John Smith**（相邻连续 8 条 Complete 订单，远多于其他客户如 Lily Potter 3 条、John Lee 4 条）。用 `done(text, success)` 返回客户名。