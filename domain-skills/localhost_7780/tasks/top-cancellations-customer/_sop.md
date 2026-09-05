# 查询历史取消订单最多的客户

前提：已登录 Magento Admin（http://localhost:7780/admin/）。

## 步骤

1. **进入订单列表**：左侧菜单点击 `Sales`（`li#menu-magento-sales-sales` 下的 `<a>Sales</a>`），展开后点击 `Orders`，或直接 `navigate` 到 `http://localhost:7780/admin/sales/order/`。

2. **打开筛选面板**：点击按钮 `Filters`（filters 面板展开前，页面顶部只有 keyword 搜索框 `placeholder="Search by keyword"`）。

3. **筛选状态为 Canceled**：在展开的筛选面板中找到 `Status` 下拉（`<select name="status">`，其 `aria-label` 形如 `notice-XXXXX` 是动态的，靠 `name=status` 定位）。执行 `select_dropdown(index, "Canceled")`，选项共 12 个：Canceled/Closed/Complete/Suspected Fraud/On Hold/Payment Review/PayPal Canceled Reversal/PayPal Reversed/Pending/Pending Payment/Pending PayPal/Processing。

4. **应用筛选**：点击按钮 `Apply Filters`。页面刷新后顶部出现 "Active filters: Status: Canceled" 与记录数（本次为 "142 records found"），全部 142 条为取消订单。

5. **遍历结果统计客户**：结果表格按 Bill-to Name 列（第 4 数据列）分组显示——同一客户的取消订单连续排列，无需逐页翻页统计。用 `scroll(amount, direction)` 向下滚动读取全部行（本页一次性渲染了全部/大部分记录，翻页控件 Previous/Next 均 disabled）。统计各客户出现次数。

6. **输出答案**：取消次数最多的客户即答案（本次记录中 Sophie Taylor 与 Sarah Miller 等靠前，以实际滚动后完整计数为准——按已观察数据，Adam Garcia/Sophie Taylor/Sarah Miller 等需全量统计后取最大者）。用 `done(text, success)` 返回客户名与取消次数。

## 注意
- 未筛选时订单共 308 条、分 2 页；筛选 Canceled 后 142 条且单页可滚动读完。
- 表格行是虚拟/懒渲染的，需持续 `scroll` 直到不再出现新行再统计。