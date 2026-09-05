# Task: Get the customer name of the most recent cancelled order (Magento admin @ localhost:7780)

Step 1: 导航到订单列表。可以直接 `navigate('http://localhost:7780/admin/sales/order/')`；若从 Dashboard 出发，则点击侧边栏菜单 `Sales`（li id=menu-magento-sales-sales 下的 `<a>`，可见文本 "Sales"），展开后点击 `Orders` 链接，进入 Sales > Orders 页面。

Step 2: 点击 "Filters" 按钮（订单列表上方工具栏，与 "Columns" / "Export" / 关键字搜索框 `id=fulltext` 同排）展开筛选面板。

Step 3: 在 Status 筛选下拉（`<select name=status>`，位于 "Status" 标签下，紧邻 "Braintree Transaction Source" 输入框之前）使用 `select_dropdown(index, "Canceled")` 选择 Canceled。可直接调用 `select_dropdown`，无需先点击打开。

Step 4: 点击 "Apply Filters" 按钮（筛选面板底部，与 "Cancel" 按钮相邻）。

Step 5: 等待结果刷新后，读取表格第一行（默认按 Purchase Date 降序，最上面的即最近取消的订单）：Bill-to Name 列即为客户名。核对该行 Status 列为 "Canceled"。若需确认可在 Dashboard 的 "Last Orders" 区域交叉验证。

Step 6: 用 `done(text=客户名, success=true)` 报告结果。