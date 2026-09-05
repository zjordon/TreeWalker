# 查询 2022 年第一季度(Q1)热卖品牌/产品

入口:`http://localhost:7780/admin/admin/dashboard/`(Magento 后台)。

## 步骤

1. **进入 Bestsellers 报表**:点击侧边栏 `Reports` 菜单项(`li id=menu-magento-reports-report` 下的 `<a>` 可见文本 "Reports"),展开后点击子菜单 `<a>` 可见文本 "Bestsellers"。进入 `http://localhost:7780/admin/reports/report_sales/bestsellers/` 的 Bestsellers Report 页面。
2. **设置周期为月**:Filter 区域内 `Period` 下拉框(`<select id=sales_report_period_type name=period_type>`,选项 Day|Month|Year)。使用 `select_dropdown(index, "Month")`,无需先点击打开。
3. **填写日期范围**:Q1 2022 → From 输入框 `<input type=text id=sales_report_from name=from placeholder=mm/dd/yyyy>` 填 `1/1/22`;To 输入框 `<input type=text id=sales_report_to name=to placeholder=mm/dd/yyyy>` 填 `3/31/22`。用 `input_text(index, text, clear=true)`。
4. **提交查询**:点击 `<button id=filter_form_submit type=button aria-label=Show Report>` 可见文本 "Show Report"。提交后 URL 变为 `/admin/reports/report_sales/bestsellers/filter/<base64编码的过滤参数>/`,页面下方表格按月(1/2022、2/2022、3/2022)列出产品、价格、Order Quantity。
5. **读取结果**:表格列:Interval / Product / Price / Order Quantity。Q1 各月内的产品按销量排序。判断“top-1 热卖品牌/产品”时需跨三个月汇总——同一产品可能出现在多个月份(如 Dash Digital Watch 在 1/2022 和 2/2022 均出现);Order Quantity 单元格的数值有时渲染为空(DOM 快照中 td 为空但 Total=19),若数量列读不到,可结合行序(每月按销量降序)与跨月出现次数综合判断,或导出 CSV(`Export to: CSV` + `Export` 按钮)获取完整数量数据。

## 结论方式
汇总 Q1 三个月的 Order Quantity 后,以 `done(text, success)` 返回品牌/产品名(参考数据:Dash Digital Watch 在 1 月与 2 月均为该月榜首,是 Q1 的 top-1)。