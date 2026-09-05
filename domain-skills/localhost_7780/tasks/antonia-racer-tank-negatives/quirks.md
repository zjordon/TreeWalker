# Quirks — localhost

- 评论行可点击区域：整行 `tr` 的 `aria-label` 即编辑页 URL（如 `http://localhost:7780/admin/review/product/edit/id/337/`），点击行内任意位置或行尾 "Edit" 链接均可进入详情页。
- 列表中的 Review 列文字被截断（以 "..." 结尾），必须进入 Edit Review 详情页（textarea `id=detail`）才能读到完整评论内容。
- Edit Review 页的 DOM 快照中 textarea `id=detail` 的文本可能显示为空，但完整评论文本实际存在于该字段的 value 中（stage 337_6 / 338_4）；若 DOM 文本为空，评论全文可从列表行可见文本或字段 value 获取。
- 详情页返回后列表会保留 `reviewGrid_filter_name` 的筛选值（URL 含 filter 参数），无需重新筛选即可继续打开下一条。