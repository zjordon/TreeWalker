# Quirks — localhost

- From/To 日期输入框带 JS 日历控件（点击会弹出日期选择 UI）。录制轨迹是「点击输入框 → send_keys Control+v 粘贴 → 再 input_text」；直接 `input_text(index, text, clear=true)` 输入 mm/dd/yy 文本同样生效（sales 阶段 value 已变为 5/1/21）。不要试图点击日期弹层中的日期格子。
- Show Report 提交是**整页跳转**（URL 变为 `/admin/reports/report_sales/sales/filter/<base64>/`），非 AJAX；提交后需重新读 DOM，旧 index 会失效。
- 结果表分批懒渲染：每次 `scroll` 后会追加更多行（如 Mar 13–Mar 28, 2022 的行在第二次滚动后出现）。若需完整数据，需多次滚动直到不再新增行。
- 默认 Order Status=Any 的统计排除已取消订单（页面文案 'Applies to Any of the Specified Order Statuses except canceled orders'）。