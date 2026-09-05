# 取消订单 302（Magento 后台）

**目标**：在 `http://localhost:7780/admin` 将订单 #000000302 状态改为 Canceled。

## 步骤

1. **进入订单列表**：点击左侧菜单 `<a>` 可见文本 "Sales"（dashboard 阶段 `id=menu-magento-sales-sales` 下的链接），再点击展开的 "Orders"。

2. **打开筛选**：在 Orders 页点击 "Filters" 按钮（`<button>` 可见文本 "Filters"，位于 "Export" 按钮旁）。

3. **按订单号筛选**：在筛选面板中找到 "ID" 输入框（`<input type=text name=increment_id maxlength=255>`，注意是订单号字段而非 "Bill-to Name" 等其他 input），用 `input_text(index, "302")` 输入订单号 302。

4. **应用筛选**：点击 "Apply Filters" 按钮（`<button>` 可见文本 "Apply Filters"）。注意不要点 "Cancel"（那是关闭筛选面板）。

5. **进入订单详情**：筛选后页面顶部显示 "Active filters: ID: 302"，点击结果行中的 `<a>` 可见文本 "View"（或直接 `navigate("http://localhost:7780/admin/sales/order/view/order_id/302/")`）。

6. **点击取消**：在订单详情页顶部按钮栏，点击 `<button id=order-view-cancel-button title=Cancel>` 可见文本 "Cancel"。

7. **确认弹窗**：弹出确认对话框后，点击 `<button type=button>` 可见文本 "OK" 确认取消。

8. **验证完成**：页面顶部出现 "You canceled the order."，Order Status 显示 "Canceled"，各商品 Item Status 变为 "Canceled"。然后调用 `done(text, success=true)`。

## 失败处理
- 若订单状态不是 Pending/Processing（如已 Complete/Closed），Cancel 按钮可能不可用——先读 Order Status 判断可取消性。