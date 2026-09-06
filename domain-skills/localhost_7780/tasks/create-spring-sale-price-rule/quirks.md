# Quirks — localhost

1. **元素 id 每次加载都随机**（new 阶段 id=XWK6F5T，重新进入编辑页后同一字段 id=MMIVG59）。定位必须用 `name` 属性（name、website_ids、customer_group_ids、simple_action、discount_amount、save、add），绝不要缓存 id。
2. **customer_group_ids 是多选 `<select>`**（stage new_2，multiselectable=true，4 个 option value=0/1/2/3）。`select_dropdown(index, value)` 每次只能选中一个值——选中全部 4 组需连续调用 4 次 select_dropdown，各传一个组的值/标签（证据中重复调用了 4 次）。website_ids 同理但只有一个选项，调用 1 次即可。
3. **Actions 区块的字段不在初始 DOM 中**（stage new_2 的快照里 Conditions/Actions/Labels 只是折叠标题 div，看不到 simple_action 和 discount_amount）。必须先 `click` 可见文本 "Actions" 的区块标题（可能需先 scroll 到页面中部）展开，字段才会渲染出来。保存前若不展开填写 simple_action，页头会出现 "This tab contains invalid data" 错误提示。
4. **保存是整页跳转**：点 Save 后回到 `/admin/sales_rule/promo_quote/` 列表页（非 AJAX），出现 "You saved the rule." 提示。之后若要核对，点击列表中对应行（`tr` 的 title 属性含 edit/id/<新id>/）进入编辑页。
5. **日期字段 from_date 默认被自动填为创建日**（编辑页 value=9/03/2026），任务未要求日期范围则无需填。