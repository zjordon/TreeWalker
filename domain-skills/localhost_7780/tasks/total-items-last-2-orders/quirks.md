# Quirks — localhost

- **Qty 数量经常为空文本**（stage 299_2 / 65_4）：每个商品行的 Qty 列嵌套 `<tr>` 中 "Ordered" 下的 `<td>`（如 [39172]）内容为空。经验规则：该 td 为空时数量按 1 计（两笔订单 5 + 4 行商品，标准答案为 9 件）。若 td 有数字则以数字为准。
- **Items Ordered 表不在首屏**：订单详情页（stage order）初始 DOM 只含头部按钮和 tabs，必须 `scroll` 2-10 个视口后才会渲染出 "Items Ordered" 与 "Order Totals" 内容（见 stage 299_2、65_4）。
- **Dashboard 的 Last Orders 的 Items 列 td 为空**（stage dashboard），不能从列表直接读件数，必须进入订单详情页。
- **返回列表用 Back 按钮**（`id=back`）或直接 `navigate` 到订单列表/下一条订单 URL；订单 URL 模式为 `/admin/sales/order/view/order_id/<id>/`，id 可从列表行 tr 的 title 属性获得。