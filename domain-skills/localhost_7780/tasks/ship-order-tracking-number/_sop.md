# 为订单添加承运商跟踪号（通过创建 Shipment）

任务：给订单（如 #299）添加 Federal Express 跟踪号。

## 步骤

1. **进入订单列表**：从 Dashboard 侧边栏点击 `Sales`，再点击子菜单 `Orders`（URL: `http://localhost:7780/admin/sales/order/`）。

2. **筛选目标订单**：点击 `Filters` 按钮展开筛选面板，在 `ID` 字段（`input name=increment_id`，如 `id=WUPF8CG`）输入订单号（如 `299`），点击 `Apply Filters`。结果列表中该订单行末尾点击 `View` 链接进入订单详情页。

3. **进入 New Shipment 页**：在订单详情页顶部按钮区点击 `Ship` 按钮（`button id=order_ship`，title=Ship）。跳转到 `http://localhost:7780/admin/admin/order_shipment/new/order_id/299/`。

4. **添加跟踪号行**：滚动到 "Shipping Information" 下方的跟踪号表格，点击 `Add Tracking Number` 按钮（`button title=Add Tracking Number`）。点击后表格新增一行：承运商下拉 `select id=trackingC1 name=tracking[1][carrier_code]`、标题输入 `input id=trackingT1`、号码输入 `input id=trackingN1 name=tracking[1][number]`、删除按钮。

5. **选择承运商并填号**：对 `trackingC1` 使用 `select_dropdown(index, value)` 选择 `Federal Express`（value=fedex；选项为 Custom Value/DHL/Federal Express/United Parcel Service/United States Postal Service）。选择 fedex 会自动填充 Title 输入框（页面 onchange 逻辑自动将选项文本写入 `trackingT1`，无需手动填）。然后用 `input_text(index, text, clear=true)` 在 `trackingN1` 中输入跟踪号（如 `8974568499`）。

6. **提交**：滚动到页面底部，点击 `Submit Shipment` 按钮（`button title=Submit Shipment`）。提交后返回订单详情页，页面顶部出现 "The shipment has been created." 提示即成功。

7. `done` 汇报成功。