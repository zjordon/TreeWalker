# Quirks — localhost

- （order 阶段）Status 筛选是原生 `<select name=status>`，用一次 `select_dropdown(index, "Complete")` 即可，不要先 click 打开。
- 筛选面板在进入订单列表页时默认是收起的，必须先点 `Filters` 按钮才会渲染出 Status 等筛选字段。
- 应用 Apply Filters 后表格刷新为 AJAX 更新，直接读取当前 DOM 即可，无需等待跳转；记录数会从 308 变为 Complete 订单数。
- "最近"的判定依据是 Purchase Date 降序（默认排序），取筛选结果前 2 行的 `Grand Total (Purchased)`（不是 Grand Total (Base)，本站两者数值相同，用 Purchased 列更符合"支付金额"语义）。
- 注意分页：若 Complete 订单超过每页条数（页码显示 "of N"），最近 2 笔一定在第 1 页。