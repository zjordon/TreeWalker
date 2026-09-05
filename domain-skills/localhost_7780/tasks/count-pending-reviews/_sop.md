# 统计所有评价中 Pending 状态的数量

Magento Admin（localhost:7780）Reviews 网格，通过状态筛选直接读出记录数。

## 步骤

1. **进入 Reviews 网格**：从 dashboard 左侧菜单点击 `Marketing`，展开后点击可见文本为 `All Reviews` 的链接，进入 `http://localhost:7780/admin/review/product/index/...`（页面标题为 Reviews，含 `New Review` / `Search` / `Reset Filter` 按钮）。
2. **清除残留筛选（如有）**：点击 `Reset Filter` 按钮（`aria-label=Reset Filter`）。
3. **筛选状态为 Pending**：对筛选行中的状态下拉 `select id=reviewGrid_filter_status name=status`（选项 Approved|Pending|Not Approved）执行 `select_dropdown(index, "Pending")`，不要先点击下拉。
4. **应用筛选**：点击 `Search` 按钮（`aria-label=Search`）。页面刷新后 URL 变为含 `filter/...status=2...` 的地址。
5. **读取结果**：网格上方分页区显示 `N records found`（本例为 5），该数字即为 Pending 评价总数。如需确认，可滚动查看列表中每行 Status 列均为 Pending，且分页显示 `of 1`（只有一页时 records found 即总数；若多页，直接以 records found 为准，无需翻页）。
6. 以 `done` 返回数量。