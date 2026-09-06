# Quirks — localhost

- **grid 控件 id 每次刷新都变**：Search / Reset Filter 按钮的 `id` 是随机串（如 `id_JBVf33VHaPZLFMMCw4zC5MCYYteI5tPW`，不同 stage 各不相同），不能依赖 id 定位；用 `aria-label=Search` / `aria-label=Reset Filter` 或可见文本定位。日期过滤输入框的 `id` 也含随机后缀（如 `reviewGrid_filter_created_at<随机>_from`），但 `name=created_at[from]` 稳定。
- **列表 Review 列被截断**：正文以 "..." 截断，判断不满程度需进入 Edit Review 详情页（点行或 Edit 链接）看完整内容；详情页 `textarea id=detail` 的文本在 DOM 树文本中不显示 value，判断依据可用 Summary of Review（`input id=title` 的 value）与 Nickname。
- **过滤保持状态**：按 name=Circe 过滤后，从详情页 `Back` 返回时过滤仍生效（URL 含 filter 参数），无需重新输入过滤条件即可看下一条。
- **一次过滤可能返回多条**：需逐条检查所有结果的 Title/Review，不能只看第一条（本例 353 与 352 均需查看）。