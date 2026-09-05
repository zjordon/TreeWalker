# Quirks — localhost

1. **筛选提交是整页跳转（非 AJAX）**：点击 "Show Report"（`filter_form_submit`）后页面导航到带 base64 filter 参数的长 URL，DOM 索引整体变化（bestsellers_2 阶段元素如 [2354]→[11840]）。提交后需重新读 DOM，不要复用旧 index。
2. **结果表格数量列可能延迟/滚动后可见**：bestsellers 阶段表格行的 Order Quantity 单元格在快照中为空文本；`scroll` 下滚后内容完整（Total=20，第一行 Quest Lumaflex™ Band）。读数前先 `scroll(2~5, down)`。
3. **日期格式为 mm/dd/yy 两位年**：from/to 输入框 placeholder 是 mm/dd/yyyy，但实际提交值 `1/1/22`、`12/31/22` 两位年份即可被接受。
4. **Dashboard 首页有同名 "Bestsellers" 标签（`a#grid_tab_ordered_products`）**，不是本报表入口；正确入口是 Reports 菜单下的 Bestsellers（bestsellers 报表页面才有 `filter_form` 与 Period/From/To 筛选器）。
5. Period 下拉用一次 `select_dropdown(index, "Year")` 即可，不要先 click（agent 内部处理开合）。