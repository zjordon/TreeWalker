# Quirks — localhost

- 结果表格（bestsellers / 最终阶段）中 "Order Quantity" 列的 `<td>` 数值为空（DOM 快照中该列无文本），排序本身就是按销量降序，因此直接按行序取前 3 行 Product 字段即可，不要因数量列为空认为表格无数据。
- 日期输入为 `input type=text` 且带日历控件按钮（From/To 旁有文本为 "undefined" 的 `<button>`）；直接用 `input_text(index, "1/1/23")` 填入文本即可，不要点击日历按钮（其内容显示为 undefined，不可靠）。
- 点击 Show Report 后是整页带参数重载（URL 变为 /bestsellers/filter/<base64>/），不是 AJAX；等待页面重新渲染后再读表格。
- Interval 列显示的是月份粒度（如 "1/2023"），可据此确认 Period=Month 已生效。