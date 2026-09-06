# 删除 Circe fleece 的所有 Pending 负面评论

前提：已在 Magento admin (`http://localhost:7780/admin/admin/dashboard/`)。

1. 导航到评论列表：侧边栏点 `Marketing` 菜单（`a` 元素，可见文本 "Marketing"），展开后点子项链接 "All Reviews"。页面标题变为 "Reviews"，URL 为 `/admin/review/product/`。
2. 先点 "Reset Filter" 按钮清除历史筛选（`button aria-label="Reset Filter"`，可见文本 "Reset Filter"）——否则之前的筛选会残留（如 name 列可能已带 value）。
3. 设置筛选：
   - `select_dropdown(index, "Pending")` 作用于 `select id=reviewGrid_filter_status name=status`（选项 Approved|Pending|Not Approved）。
   - `input_text(index, "Circe", clear=true)` 作用于 `input id=reviewGrid_filter_name name=name type=text`（Product 列筛选，输入产品名片段即可）。
4. 点 `button aria-label="Search"`（可见文本 "Search"）提交筛选。结果表格只显示 Circe 相关评论。
5. 逐条核对：只删除 Status 列为 "Pending" 且内容为负面（如标题 "Bad!"、评论文案为失望类）的行。用行内 `a` 可见文本 "Edit" 进入编辑页（URL 形如 `/admin/review/product/edit/id/353/`）。
6. 在编辑页点 `button id=delete aria-label="Delete Review"`（可见文本 "Delete Review"）。页面弹出确认对话框（`aside role=dialog`，文本 "Are you sure you want to do this?"），点其中的 "OK" 按钮（`button`，可见文本 "OK"）。
7. 删除后返回评论列表，顶部出现提示 "The review has been deleted."，且筛选结果中该行消失（"We couldn't find any records." 表示全部删完）。
8. 若还有待删除的 Pending 负面评论，重复步骤 5-7；全部删完后 `done(text, success)`。