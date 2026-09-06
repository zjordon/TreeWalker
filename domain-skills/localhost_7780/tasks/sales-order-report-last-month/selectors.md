# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| From 日期输入 | Orders Report 页 filter_form 内, "From" 标签后 | `id=sales_report_from, name=from, type=text, title=From` | 格式 m/d/yy |
| To 日期输入 | "To" 标签后 | `id=sales_report_to, name=to, type=text, title=To` | 输入后可能需 blur 才生效 |
| 提交按钮 | 表单顶部 | `id=filter_form_submit, 可见文本"Show Report"` | 整页刷新,非 AJAX |