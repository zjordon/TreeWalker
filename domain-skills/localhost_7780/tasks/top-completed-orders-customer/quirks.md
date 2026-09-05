# Quirks — localhost

- **筛选结果默认按 Bill-to Name 分组**：筛选 Status=Complete 后，同一客户的订单相邻排列（order_3/order_4 阶段可见 Sarah Miller、John Smith 等连续成块），可直接数连续块长度，无需逐行建表。
- **Apply Filters 触发整页刷新**（order_2→order_3 阶段），record count 从 308 变为 153；操作后需重新读取 DOM，旧 index 全部失效。
- **残留筛选**：进入订单页可能带旧筛选（order 阶段已有 "Active filters: Status: Complete"），重复筛选前先点 Remove/Clear all，否则结果叠加。
- **每页仅显示 20 行**，153 条需通过分页（"of 8" 页）+ scroll 遍历完才能确认最值，勿只看第一页。