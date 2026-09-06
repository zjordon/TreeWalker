# Quirks — localhost

- 编辑页的 "Delete Review" (`button id=delete`) 会弹出一个 `aside role=dialog` 确认框（"Are you sure you want to do this?"），必须再点对话框内的 "OK" 按钮才真正删除；不点 OK 则不生效。该对话框是删除后才插入 DOM 的。
- 删除是整页跳转回评论列表（非 AJAX），成功标志是顶部消息 "The review has been deleted."；删除后需重新检查剩余筛选结果再决定是否继续。
- 筛选前先点 "Reset Filter"，否则前一次会话的筛选条件（如 name 列残留 value）会让结果不符预期（见 stage dashboard_1 中 name 列已有 value=Circe）。
- 判断"负面"需读行内 Review 摘要文本（如 "I was really disappointed..."）；Status=Pending 是必要条件但不等于负面。