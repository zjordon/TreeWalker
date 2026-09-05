# Approve positive pending reviews

目标：在 Magento 后台把 Pending 状态的正面评价改为 Approved，使其在前台展示。负面评价（如标题 "Bad!"、"won't recommand"）不要批准。

## Step 1: 进入评价列表
- 从 Dashboard 侧边栏 `click` 可见文本 "Marketing" 的 `<a>`，再 `click` 可见文本 "All Reviews" 的 `<a>`，进入 `/admin/review/product/`（Reviews 列表页）。

## Step 2: 过滤出 Pending 状态的评价
- 点击 `aria-label=Reset Filter` 的 `button`（"Reset Filter"）清掉旧过滤条件。
- 对状态过滤下拉 `select id=reviewGrid_filter_status`（在表格过滤行 "Status" 列下，options=Approved|Pending|Not Approved）执行 `select_dropdown(index, "Pending")`。
- `click` `aria-label=Search` 的 `button`（"Search"）应用过滤。URL 会变为含 `filter/...status=2...` 的形式，列表只显示 Pending 评价（本例共 5 条：353/352/351/349/347）。

## Step 3: 逐条判断并批准正面评价
对每一行：阅读 'Summary Rating' 判断情感。
- 正面（3 星以上包括 3 星）→ 进入编辑并批准。
- 负面（3 星以下不包括 3 星）→ 跳过（可点开确认后 `click` `button id=back`（"Back"）返回列表）。

进入编辑：`click` 该行 Action 列的 `a` 可见文本 "Edit"（每行 `<tr>` 的 `title` 属性含 edit URL，如 `.../edit/id/352/`，可用于核对行身份）。可能需 `scroll` 到该行。

## Step 4: 在 Edit Review 页修改状态并保存
- 编辑页（URL `.../review/product/edit/id/<ID>/`）"Status" 字段为 `select id=status_id`（options=Approved|Pending|Not Approved）。执行 `select_dropdown(index, "Approved")`（无需先点击打开）。
- `click` `button id=save_button`（aria-label="Save Review"）保存。
- 保存后整页跳回 Reviews 列表，页面顶部出现 "You saved the review." 提示——以此确认保存成功。
- 回到列表后继续处理下一条正面 Pending 评价，重复 Step 3–4。

## Step 5: 完成
所有正面 Pending 评价处理完后 `done`。

注意：保存返回列表后过滤条件可能保留（仍只显示 Pending），剩余未批准的即负面评价；若列表被重置，重新执行 Step 2 过滤。