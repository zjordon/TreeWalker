# Quirks — localhost

1. **跟踪号行是动态模板**：在 New Shipment 页（stage 299_5），承运商下拉和号码输入框默认不在 DOM 中，必须先点击 `Add Tracking Number` 按钮才会插入一行；每行的 id/name 后缀数字递增（第一行为 `trackingC1`/`trackingN1`，`name=tracking[1][...]`）。
2. **选择承运商自动填充 Title**：页面 onchange 逻辑会在选择非 custom 承运商时自动把选项文本（如 "Federal Express"）写入 `trackingT1` 标题输入框，无需手动填写标题。
3. **该任务必须走 Ship（创建 Shipment）流程**：Magento admin 没有独立的"添加跟踪号"入口；跟踪号只能在创建 shipment 时附带提交，`Submit Shipment` 后订单会出现 shipment 且订单状态可能从 Pending 变化。若订单已有 shipment，需改从订单详情页 `Shipments` 标签进入已有 shipment 编辑跟踪号。