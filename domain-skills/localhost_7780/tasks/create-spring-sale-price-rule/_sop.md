# 在 Magento 后台创建全站 20% 折扣的 Cart Price Rule（spring sale）

目标：新建一条名为 "spring sale" 的 Cart Price Rule，对 Main Website 所有客户组（含未登录）生效，动作类型为 Percent of product price discount，折扣值 20。

## 步骤

1. **进入 Cart Price Rules 列表**：在后台左侧菜单点击 `Marketing`（`<a>` 可见文本 "Marketing"），展开后点击子菜单 `<a>` 可见文本 "Cart Price Rules"，进入 `/admin/sales_rule/promo_quote/`。
2. **新建规则**：点击右上角 `<button id=add title="Add New Rule">` 可见文本 "Add New Rule"，进入 New Cart Price Rule 页面（URL `/admin/sales_rule/promo_quote/new/`）。
3. **填写 Rule Information**：
   - Rule Name：`<input type=text name=name maxlength=255>`，`input_text` 填 "spring sale"。
   - Active 复选框默认已勾选（`name=is_active checked=true`），无需改动。
   - Websites：多选 `<select name=website_ids>`，选中 "Main Website"（唯一选项）。
   - Customer Groups：多选 `<select name=customer_group_ids>`，只需选中除 NOT LOGGED IN 外的其它三个组（见 quirks — 需多次调用 select_dropdown，每次只能选中一个值）。
   - Coupon 保持默认 "No Coupon"。
4. **切换到 Actions 区块**：页面上 Actions 只是一个折叠区块标题（可见文本 "Actions"，伴随提示 "Changes have been made to this section that have not been saved"）。需 `click` 该 "Actions" 区块标题展开（页面很长，先 `scroll(4, down)`）。展开后：
   - Apply：`<select name=simple_action>`，选项有 "Percent of product price discount" / "Fixed amount discount" 等，选中 "Percent of product price discount"。
   - Discount Amount：`<input type=text name=discount_amount>`，`input_text` 填 "20"。
5. **保存**：点击顶部 `<button id=save title=Save>` 可见文本 "Save"。保存成功后回到 `/admin/sales_rule/promo_quote/` 列表页并出现 "You saved the rule." 提示。
6. **验证**：在列表中找到规则名为 "spring sale"、状态 Active、Web Site 为 Main Website 的新行（可能点击该行进入编辑页 `/admin/sales_rule/promo_quote/edit/id/<新id>/` 检查 Rule Information 与 Actions 是否保存正确）。确认无误后 `done(text, success)`。