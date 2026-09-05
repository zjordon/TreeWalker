# Quirks — localhost

- **排序方向**（order_2 阶段）：订单网格默认按 Purchase Date **降序**（最新在前）。要找"最早"订单，必须点击 "Purchase Date" 表头一次切换为升序；只筛选不排序会拿到最新的完成订单。
- **筛选遗留状态**：订单页可能残留上次的 Active filters（如 Status: Canceled）。进入后先检查 "Active filters" 文案并点 "Clear all"，否则新筛选结果会与旧条件叠加。
- **记录数差异可作校验**：Apply Filters 后顶部 "records found" 数量会变化（全量 142 → 完成订单数），可用于确认筛选生效后再排序。