# Quirks — localhost

1. **Order Quantity 数值在 DOM 快照中可能为空**(bestsellers / filter 结果页 stage):结果表格的 Order Quantity `<td>` 渲染为空,仅 Total 行显示总数 19。行序按每月销量降序排列,跨月比较需按 Interval 分组,每月第一行即该月 top-1。若必须精确数量,用页面的 `Export to: CSV` 导出。
2. **“品牌”实为“产品”**:Bestsellers 报表按具体产品(SKU 变体,如 "Dash Digital Watch")统计,不提供品牌维度;回答时给出产品名并说明其品牌即可(如 Dash Digital Watch → 品牌 Dash)。
3. **日期输入无日期选择器交互需求**:`input_text` 直接写入 `1/1/22` 格式即可,旁边会渲染一个日历图标按钮(span 文本 "undefined"),无需点击它。