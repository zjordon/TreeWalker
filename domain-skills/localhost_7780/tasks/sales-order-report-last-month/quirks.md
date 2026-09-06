# Quirks — localhost

- (stage sales_2) 日期输入框带日历控件: 每个日期 input 旁会出现一个日历切换 `<button>`(其 span 文本为 "undefined")。直接 `input_text` 写入日期即可, 不要点日历按钮。若 "To" 值未生效(输入后 value 未更新), 需重新点击该 input 再 `input_text`, 必要时点击 `form id=filter_form` 空白处触发 blur 关闭日历 —— 证据中 To 字段多次重试后才成功。
- `Show Report`(`id=filter_form_submit`) 是整页导航(跳转到 `/sales/filter/<base64>` URL), 不是 AJAX; 提交后等待页面加载, 依据 "N records found" 与 Total 行出现判断完成。
- 报告默认无日期过滤时显示 "We couldn't find any records.", 必须先填 From/To 再提交才有数据。
- "last month" 需自行按任务给定日期换算为上月起止日(如 3/15/2023 → 2/1/23 ~ 2/28/23), 表单日期格式为 m/d/yy。
- 结果表格部分列(Orders、Sales Items)的单元格在 DOM 中可能为空 td, 仅有金额列有值; 汇总请以 Total 行为准。