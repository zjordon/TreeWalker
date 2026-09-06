# Quirks — localhost

- **详情页 Grand Total 与订单信息不在同一屏**：发票详情页很长，"Invoice Totals"（含 Grand Total 行）在页面最底部，需 `scroll` 多次才能在 DOM 中看到该行（stage invoice_2 → 1_3 才出现）。
- **两个 Grand Total 来源**：发票列表页行内 "Grand Total (Base)" / "Grand Total (Purchased)" 列已给出金额；详情页 "Invoice Totals" 区块的 "Grand Total" 行是权威来源，两者应一致，可交叉验证。
- **搜索后无需点击 View 链接**：点击发票号 `<td>` 本身即可进入详情页（stage invoice_2 证实），不必找行内 "View" 链接。