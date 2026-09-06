# Update Antonia Racer Tank description with positive review quotes (Magento admin)

目标：把正面评价的原文引用加入 Antonia Racer Tank 的 Short Description，保存并刷新缓存。正面引用（取自评价 #339 和 #338）：
- "This is in regular rotation at the gym. Its colorful and looks kinda cute under my exercise tanks."
- "it's very stylish for yoga or something else low impact."

## Step 1: 收集正面评价原文（Marketing > All Reviews）
1. 在 admin 侧栏点击 `Marketing`，再点 `All Reviews`（可见文本），进入 `/admin/review/product/`。
2. 在筛选行的 Product 列筛选框 `input#reviewGrid_filter_name name=name` 中输入 `Antonia Racer Tank`，点 `Search` 按钮（aria-label=Search）。结果会只剩 3 条评价（ID 337/338/339，均 Approved）。337 是差评（Zero support/modesty），338/339 是可引用的正面评价。
3. 点行尾 `Edit` 链接进入 `/admin/review/product/edit/id/339/`，完整评论正文在 `textarea id=detail name=detail` 中（列表里被截断，须进编辑页取全文）。记录后点 `Back`（button id=back）返回列表。
4. 同样打开 ID 338 的 Edit 页取全文，点 `Back` 返回。

## Step 2: 打开产品编辑页（Catalog > Products）
5. 侧栏点 `Catalog` → `Products`，进入产品网格。
6. 在关键字搜索框 `input#fulltext placeholder="Search by keyword"` 输入 `Antonia Racer Tank`，点 Search 按钮。结果中点 `a aria-label="Edit Antonia Racer Tank"`（可见文本 Edit）进入 `/admin/catalog/product/edit/id/1796/`。

## Step 3: 编辑 Short Description（Content 区）
7. 页面很长；展开 `Content` 区（stage 1796_10 中点击 Content 区块标题）。Content 区含 "Short Description" TinyMCE 编辑器（iframe `title="Rich Text Area" id=product_form_short_description_ifr`）和其下的 "Description" 字段。本次证据改的是 Short Description（前台 PDP 顶部显示的三行引用文字即来自它）。
8. 把正面引用文字写入 Short Description（HTML 段落，如 `<p>...</p>`，每个引用一段）。
9. 点 `button id=save-button`（可见文本 Save，页面右上）保存。若 Content 标签出现 "Changes have been made to this section that have not been saved. This tab contains invalid data." 的红色提示 div，说明该 tab 有未保存/无效数据——须先在该 tab 内解决（通常是必填字段缺失），否则保存会被拦截。

## Step 4: 刷新缓存（必须，否则前台不更新）
10. 侧栏 `System` → `Cache Management`（/admin/admin/cache/），点 `button id=flush_magento`（"Flush Magento Cache"）。看到 "The Magento cache storage has been flushed." 即成功。

## Step 5: 验证前台
11. `navigate` 到 `http://localhost:7780/antonia-racer-tank.html`，滚动到 Add to Cart 区域下方，确认引用文字出现在 Short Description 位置（"This is in regular rotation at the gym / Its colorful and looks kinda cute under my exercise tanks. / it's very stylish for yoga or something else low impact."）。Details 标签里的 Description 保持原文不变。完成后 `done`。