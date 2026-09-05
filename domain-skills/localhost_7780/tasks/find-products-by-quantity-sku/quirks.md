# Quirks — localhost

- **筛选面板输入框的 id 每次页面加载随机生成**（product_2 阶段 qty[from] id=B6B20FH，重录时会是别的串）。定位必须靠 `name=qty[from]` / `name=qty[to]`，绝不要硬编码 id。
- **筛选面板在点击 "Filters" 按钮之前不在 DOM 中**（product 阶段面板字段不存在，product_2 阶段才出现）。必须先 `click` "Filters" 再找输入框。
- **Apply Filters 是 AJAX 局部刷新，URL 不变**。点击后需等待网格重新加载（可短暂 `wait` 后重读 DOM）；结果是否为空看网格是否出现 "We couldn't find any records." 或分页 "of 1"。
- 精确数量匹配需 **from 和 to 都填同一个值**，否则是范围查询。