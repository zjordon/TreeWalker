# 在 Magento 后台创建全站 20% 折扣的 Cart Price Rule（spring sale）

目标：新建一条名为 "spring sale" 的 Cart Price Rule，对 Main Website 所有客户组（含未登录）生效，动作类型为 Percent of product price discount，折扣值 20。

## 步骤

1. **进入 Cart Price Rules 列表**：在后台左侧菜单点击 `Marketing`（`<a>` 可见文本 "Marketing"），展开后点击子菜单 `<a>` 可见文本 "Cart Price Rules"，进入 `/admin/sales_rule/promo_quote/`。
2. **新建规则**：点击右上角 `<button id=add title="Add New Rule">` 可见文本 "Add New Rule"，进入 New Cart Price Rule 页面（URL `/admin/sales_rule/promo_quote/new/`）。
3. **填写 Rule Information**：
   - Rule Name：`<input type=text name=name maxlength=255>`，`input_text` 填 "spring sale"。
   - Active 复选框默认已勾选（`name=is_active checked=true`），无需改动。
   - Websites：多选 `<select name=website_ids>`，选中 "Main Website"（唯一选项）。
   - Customer Groups：多选 `<select name=customer_group_ids>`，只需选中除 NOT LOGGED IN 外的其它三个组，用 `select_dropdown(index, values=[...])` 一次传全部目标组（整组替换语义，见 quirks——逐次单选只剩最后一个）。
   - Coupon 保持默认 "No Coupon"。
4. **切换到 Actions 区块**：页面上 Actions 只是一个折叠区块标题（可见文本 "Actions"，伴随提示 "Changes have been made to this section that have not been saved"）。需 `click` 该 "Actions" 区块标题展开（页面很长，先 `scroll(4, down)`）。展开后：
   - Apply：`<select name=simple_action>`，**按任务措辞选判分对应的动作类型**（措辞→选项映射，选项文本以页面实际为准）：

     | 任务措辞 | UI 选项 | simple_action |
     |---|---|---|
     | N% off / percent discount site-wide | Percent of product price discount | by_percent |
     | $X off each item / per item | Fixed amount discount | by_fixed |
     | $X off checkout / whole cart / order total / $X discount on checkout | Fixed amount discount for whole cart | cart_fixed |

     本例（20 percent discount）选 "Percent of product price discount"。
   - Discount Amount：`<input type=text name=discount_amount>`，`input_text` 填 "20"。
5. **保存**：点击顶部 `<button id=save title=Save>` 可见文本 "Save"。保存成功后回到 `/admin/sales_rule/promo_quote/` 列表页并出现 "You saved the rule." 提示。
6. **验证**：在列表中找到规则名为 "spring sale"、状态 Active、Web Site 为 Main Website 的新行（可能点击该行进入编辑页 `/admin/sales_rule/promo_quote/edit/id/<新id>/` 检查 Rule Information 与 Actions 是否保存正确）。确认无误后 `done(text, success)`。