# Quirks — localhost

- **表格单元格文本偏移**（dashboard/dashboard_2 阶段）：Top Search Terms 与 Last Search Terms 表格中，许多 `<td />` 在 DOM 树文本里显示为空节点，数值实际渲染在相邻的空文本 `td` 中（如 "Antonia Racer Tank" 行：Results=23 出现在其后一个空 `td` 位置）。读数时按行的 `title=".../search/term/edit/id/N/"` 对齐单元格顺序（Search Term → Results → Uses），不要因某 `td` 无文本而误判该列为空。
- Dashboard 页首的 Revenue/Bestsellers 区块与目标无关，直接滚到底部找 "Top Search Terms"（注意区分其上方的 "Last Search Terms" 表，两者结构相同，`id` 分别为 `topSearchGrid_table` / `lastSearchGrid_table`）。