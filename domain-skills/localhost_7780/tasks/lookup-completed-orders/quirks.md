# Quirks — localhost

- **残留筛选状态**（order → order_3 阶段验证）：订单列表会保留上一次会话的 active filter（证据中进入时已带 "Status: Suspected Fraud"）。应用新的 Complete 筛选后若结果显示 "We couldn't find any records."，需点击 "Clear all" 清除旧筛选再重选，否则多状态叠加导致结果为空。
- **select_dropdown 直接用**：Status 是 `<select name=status>`，一步 `select_dropdown(index, "Complete")` 即可，无需先 click。
- 筛选结果条数（records found）在 Apply Filters 后通过完整页面刷新更新，非纯 AJAX 局部更新；应用后重新读取 DOM 即可。