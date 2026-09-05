# 修改 404 Not Found 页面标题

前置：已在 Magento 后台 Dashboard（http://localhost:7780/admin/admin/dashboard/）。若不在，先 `navigate` 到该地址。

1. 在左侧主导航点击 Content（`li#menu-magento-backend-content` 下的 `<a>` 可见文本 "Content"），展开子菜单。
2. 点击子菜单中的 Pages（`<a>` 可见文本 "Pages"），进入 CMS 页面列表（Content > Elements > Pages）。
3. 在页面列表中找到标题为 "404 Not Found"（URL Key 为 no-route，ID 1）的行，点击该行 Action 列的 Edit 链接（`<a>` 可见文本 "Edit"）。注意只有 404 Not Found 行展开了 Edit/Delete/View 链接，其他行需要先点行尾 Select 按钮展开。
4. 进入编辑页（URL: /admin/cms/page/edit/page_id/1/）后，Page Title 输入框在 "Page Title" 标签旁，为 `<input type=text id=YA7B21G name=title maxlength=255>`。使用 `input_text(index=…, text="Bruh bro you clicked the wrong page", clear=true)` 清空并填入新标题。
5. 点击右上角保存按钮：`<button id=save-button title=Save aria-label=Save>` 可见文本 "Save"，用 `click` 提交。保存成功后页面会刷新/提示，任务完成，调用 `done`。