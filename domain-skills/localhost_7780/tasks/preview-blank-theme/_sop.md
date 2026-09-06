# 预览 Magento Blank 主题

**前置**：已登录 Magento Admin（入口 `http://localhost:7780/admin/admin/dashboard/`）。

**步骤 1**：在 Dashboard 左侧主菜单，点击 `Content` 菜单项（`<li id=menu-magento-backend-content>` 下的 `<a>`，可见文本 "Content"）。

**步骤 2**：在展开的 Content 子菜单中，点击可见文本为 "Themes" 的 `<a>` 链接，进入 Themes 列表页（URL 变为 `http://localhost:7780/admin/admin/system_design_theme/`）。

**步骤 3**：在 Themes 列表中找到 "Magento Blank" 对应的行，点击该行的可见文本 "View" 链接（`<a data-tw-jsclick=1>`）。进入主题编辑页后，页面顶部标题区显示 "Theme: Magento Blank"，即完成预览。可点击左上角 `Back` 按钮（`<button title=Back id=back>`）返回列表。

**完成**：调用 `done(text, success)`。