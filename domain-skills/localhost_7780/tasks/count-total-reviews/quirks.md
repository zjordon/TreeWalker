# Quirks — localhost

- **总数来源**：不要数表格行（每页仅 20 行，共 4 页）。总评论数直接读网格上方工具栏的 "N records found" 文本（本次录制值为 351），该计数已包含全部状态（Approved/Pending/Not Approved），无需翻页或过滤。
- **过滤残留**：若之前会话设置过过滤器，评论数可能偏小；点击 "Reset Filter" 按钮可恢复全量计数（URL 会变为 `.../review/product/index/filter//internal_reviews//form_key/...`）。