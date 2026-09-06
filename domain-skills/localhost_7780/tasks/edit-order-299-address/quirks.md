# Quirks — localhost

- **必须分别编辑两个地址**：订单详情页的 Billing Address 与 Shipping Address 是两条独立记录（address_id 598 和 597），各有独立的 Edit 入口和保存操作。只改一个会导致另一个仍是旧地址。
- **地址保存不重算金额**：表单上方提示 "Changing address information will not recalculate shipping, tax or other order amount."，属正常现象，不影响保存。
- **"order #299" 是 increment_id 000000299**：筛选框 `name=increment_id` 输入 `299` 或 `000000299` 都能命中（证据中输入 299 成功）。
- **保存后整页跳转**：点击 Save Order Address 会返回订单详情页（URL 变为 `/sales/order/view/order_id/299/`），出现 "You updated the order address." 消息，无需额外等待弹窗。
- **region 字段双形态**：United States 下 State/Province 有 `select#region_id`（隐藏的 select，defaultValue=12 即 California）和文本框 `input#region`。shipping 地址编辑（stage 597_7）中实际是向 `input#region` 输入 "New York" 生效；billing（598_5）中用 select_dropdown 选 region_id。若 select_dropdown 对 region_id 不生效，改用 `input_text` 写 `input#region`。
- **street 字段必填两条**：street[0] 与 street[1] 均 required=true，地址需拆成 "456 Oak Avenue" / "Apartment 5B" 两行填入。