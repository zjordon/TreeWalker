# Quirks — localhost

- Dashboard 页面（dashboard 阶段）有两个极易混淆的相邻表格：上为 "Last Search Terms"（`id=lastSearchGrid_table`），下为 "Top Search Terms"（`id=topSearchGrid_table`）。本任务答案必须取自 **topSearchGrid_table** 的第一个数据行，两表内容相似（都含 Antonia/tanks/hollister 等词）但排序不同。
- 表格位于页面底部，初始 DOM 快照可能未展开/需滚动后渲染；若快照中未见 `topSearchGrid_table`，先 `scroll` 再重读 DOM。
- Dashboard 的 Revenue/Lifetime Sales 等指标均显示 $0.00，属站点数据现状，不代表加载失败。