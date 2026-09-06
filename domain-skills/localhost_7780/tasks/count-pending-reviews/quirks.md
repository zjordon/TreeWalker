# Quirks — localhost

- 结果数字 `records found` 前的数值位于分页条内（`reviewGrid_page-limit` 下拉之前的文本节点），筛选后必须以它为准，而不是数当前页行数——即使多页，records found 也是全量计数，无需翻页累加。
- 筛选后页面 URL 变化（含 base64 的 filter 参数），但 DOM 元素 id 保持 `reviewGrid_filter_status` 等不变；重新识别元素时按 id/name 找，不要依赖旧 index。
- 进入 All Reviews 前如网格显示的是上次的筛选结果（如 dashboard_1 阶段仅显示 5 条），先点 `Reset Filter` 再设置状态筛选，否则计数会被旧筛选污染。