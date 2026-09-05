# 任务：查看店内排名第一的搜索词（Top Search Terms 第 1 名）

## 步骤

1. 进入 Admin 后台 Dashboard：直接 `navigate` 到 `http://localhost:7780/admin/admin/dashboard/`；或从任意后台页面点击左侧菜单 `<li id=menu-magento-backend-dashboard>` 下的 `<a>` 可见文本 "Dashboard"。
2. 在 Dashboard 页面向下 `scroll`（约 2-3 个视口），直到页面下方出现两个表格："Last Search Terms"（`<table id=lastSearchGrid_table>`）和 "Top Search Terms"（`<table id=topSearchGrid_table>`）。
3. 目标数据在 **Top Search Terms** 表（注意不要读错成上方的 Last Search Terms 表）。表头为 Search Term / Results / Uses。第一行数据行（`<tr>` 带指向 `/admin/search/term/edit/id/...` 的 title 属性）即为排名第 1 的搜索词。本次录制中第 1 行为 "hollister"（Uses=19），其后依次为 Joust Bag、Antonia Racer Tank、Antonia、tanks。
4. 读取第 1 行 Search Term 单元格文本，用 `done(text, success)` 返回该搜索词名称。