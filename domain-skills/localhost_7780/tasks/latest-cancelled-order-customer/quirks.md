# Quirks — localhost

- 订单列表默认按 Purchase Date 降序排列，因此筛选 Canceled 后表格第一行即最近的取消订单（本次数据中为 Lily Potter，订单 000000136，May 23, 2023）。这是 DOM 可见的，但"第一行=最新"这一排序约定是判断依据。
- Status 下拉是一个含 12 个选项的 select（含 Canceled/Closed/Complete/Suspected Fraud 等），使用 `select_dropdown(index, "Canceled")` 直接选择，注意选项值拼写为 "Canceled"（单 l）。
- 点击 Apply Filters 后表格为 AJAX 局部刷新，records found 数字会变化；若行数据未更新，先 `wait(1-2)` 再读取。