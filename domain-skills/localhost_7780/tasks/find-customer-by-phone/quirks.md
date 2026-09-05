# Quirks — localhost

1. **过滤输入框 id 每次加载随机**（如 stage=index 中 `id=ETDWRK7`）：所有过滤输入框的 id 都是随机生成串，不能作为身份依据，必须靠 `name=billing_telephone` 等稳定 name 定位。
2. **电话过滤匹配要求纯数字格式**：用户给的号码可能带 "+1 " 前缀（+1 2058812302），但 `billing_telephone` 过滤需输入去掉前缀的 10 位数字（2058812302），否则结果为空（stage=index_3 显示 Active filters: Phone: 2058812302 且仅命中 1 行）。
3. **Apply Filters 是整页刷新**（stage=index_2 → index_3）：点击后页面重新加载，DOM index 全部失效，需重新读 DOM 再取结果行。
4. 过滤面板默认收起，`billing_telephone` 输入框在点击 "Filters" 按钮之前不在 DOM 中（对比 stage=dashboard 后的客户列表初始态）。