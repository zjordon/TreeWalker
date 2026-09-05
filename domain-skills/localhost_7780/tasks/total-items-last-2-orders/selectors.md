# Selectors — localhost

所有元素在 sop_md 中已就地描述（订单行以 tr 的 title 属性 URL 识别；Back 按钮有 id=back）。唯一需要指纹的是 Qty 单元格：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Items Ordered 表中每行的购买数量 | "Items Ordered" 表头行之后，每商品行内 Price 列后的 `<td>` 中嵌套 `<tr>`，其内有文本 "Ordered"，数量在其后的 `<td>` | 可见文本"Ordered"（嵌套 tr） | 数量是嵌套 tr 中 Ordered 后的 td 文本（本例均为空/1，值为空时按 1 计）；不要误读 Subtotal/Tax 等金额列 |
| 订单详情行入口 | Orders 列表每行最右列 | 可见文本"View" | 点击进入该订单详情页 |