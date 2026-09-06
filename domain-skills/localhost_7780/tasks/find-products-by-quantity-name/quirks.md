# Quirks — localhost

- 进入 product 页面时可能残留上次的筛选（页面显示 "Active filters: Quantity:" 标签）。必须先点该标签旁的 `Remove` 按钮清除，再点 `Filters` 重新打开面板填写，否则筛选输入框可能不出现或结果被旧条件污染。
- 填写 qty 筛选时事件日志显示先点击输入框再 input；直接 `input_text(index=…, text="0")` 到 `name=qty[from]` 和 `name=qty[to]` 两个框即可（都填 0 表示精确等于 0）。
- 筛选输入框的 `id` 每次页面加载都会随机生成（PHS74DC、SQ57YUG 等），跨会话 index/id 不稳定，靠 `name` 属性在 DOM 文本中识别。
- 结果可能分页（如 2040 条记录 11 页，每页 20 条）；需点 Next Page 遍历所有页收集产品名，否则会漏报。