# localhost (Magento 2.4.6) 站点卡片

## 站点功能地图（Site Function Map）

- 管理后台入口: `http://localhost:7780/admin/`（登录用户 admin）；店面入口 `http://localhost:7780/`
- Dashboard: `/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Last Orders 表——行 tr title 直接是订单详情 URL）
- 商品管理: `Catalog` → `Products` → `/admin/catalog/product/`（约 2040 条；`id=add_new_product`，下拉含 Simple/Configurable/Grouped/Virtual/Bundle/Downloadable）
- 商品编辑: `/admin/catalog/product/edit/id/N/`；顶部 Save(`id=save-button`)/Back(`id=back`)/Add Attribute(`id=addAttribute`)；字段靠 `name=product[...]`
- 可配置商品变体（编辑页 Configurations 区）：`Edit Configurations` 三步向导 → `Generate Products`；矩阵输入 `name=configurable-matrix[N][...]`
- 订单: `Sales` → `Orders`（308 条，`id=add`=Create New Order）
- 订单详情: `/admin/sales/order/view/order_id/N/`；顶部 Back/`order_edit`/`order_invoice`/`order_ship`/`order_reorder`/Send Email/Hold/Cancel(`id=order-view-cancel-button`)；左侧标签 Information/Invoices/Credit Memos/Shipments/Comments History
- 订单地址编辑: `/admin/sales/order/address/address_id/N/`（详情页地址块 "Edit" 进入）
- 新建发货: `/admin/admin/order_shipment/new/order_id/N/`（详情页 `id=order_ship` 进入）；含追踪号表 + Items to Ship 数量 + `Submit Shipment`
- 客户: `Customers` → `All Customers` → `/admin/customer/index/`
- 商品评论: `Marketing` → `Reviews` → `All Reviews` → `/admin/review/product/index/`（约 351 条）；编辑/删除 `/admin/review/product/edit/id/N/`（`id=delete`）
- 购物车价格规则: `Marketing` → `Cart Price Rules` → `/admin/sales_rule/promo_quote/`；新建/编辑 `/admin/sales_rule/promo_quote/new/`、`/edit/id/N/`
- CMS 页面: `Content` → `Pages` → `/admin/cms/page/`（网格列 ID/Title/URL Key/Layout/Store View/Status/Created/Modified；`id=add`=Add New Page）；编辑 `/admin/cms/page/edit/page_id/N/`（顶部 Back(`id=back`)/Delete Page(`id=delete`)/Save(`id=save-button`)；字段 `name=title`（Page Title）、checkbox `name=is_active`；分区折叠：Content/SEO/Page in Websites/Design/Custom Design Update）
- 主题管理: `Content` → `Design` → `Configuration`/`Themes`；主题列表含行尾 "View"；主题详情 `/admin/admin/system_design_theme/`（如 Theme: Magento Blank，顶部 `id=back` Back，标签 `id=theme_tabs_general_section` General）
- 缓存管理: `System` → `Cache Management`（`id=flush_magento`、`id=flush_system`）
- 报表: `Reports` → Products→Bestsellers、Sales→Orders/Refunds
- 侧边菜单（li id 稳定）: Dashboard/Sales/Catalog/Customers/Marketing/Content/Reports/Stores/System/Find Partners & Extensions
- 页头全局搜索 `id=search-global`；UI 网格右上 `id=fulltext`；页头有 Scope 切换（store/group/website switcher）与 Notifications
- 店面商品页标签: `Details`(`id=tab-label-description-title`)/`More Information`/`Reviews`

## 站点通用操作知识

- 侧边菜单两级：点一级展开后二级才出现；每步点击后重读 DOM；跨区直接点侧边菜单。
- 三类网格/表单：新 UI-Component 网格（订单/客户/商品/CMS）Filters 同页 AJAX、`Apply Filters`/`Cancel`；旧版网格（评论 reviewGrid、促销规则 promo_quote_grid）筛选行在表头下，`title=Search` 提交、`Reset Filter` 清除、整页跳转；报表 `filter_form` + `filter_form_submit`。
- 旧网格筛选输入靠稳定 id（如 `promo_quote_grid_filter_rule_id`/`_name`/`_coupon_code`/`_sort_order`，日期对 `from_date[from]/[to]`、`to_date[from]/[to]` placeholder=mm/dd/yyyy，下拉 `promo_quote_grid_filter_is_active`/`_rule_website`）。
- 订单网格 Filters 字段（name 稳定）：`increment_id`/`billing_name`/`shipping_name`/`created_at[from]/[to]`/`base_grand_total[from]/[to]`/`grand_total[from]/[to]`/`transaction_source`/下拉 `store_id`/`status`（12 状态）。填后点 "Apply Filters"，看 "N records found"；行尾 "View" 进详情，行 `tr title` 含详情 URL 可直接 navigate。翻页 title=Next/Previous Page。
- 按订单号找单：Filters → `increment_id` 输入数字（如 299 匹配 000000299）→ Apply Filters → 行尾 View；应用后出现 "Active filters" 与 "Clear all"。
- 评论筛选字段（id 稳定）：`reviewGrid_filter_name`/`_sku`/`_title`/`_nickname`/`_detail`/`_review_id`/`_status`/`_type`；日期 `name=created_at[from]/[to]`；下拉用 `select_dropdown(index, value)`。
- **CMS 页面网格**（page 阶段）：新 UI-Component 网格，筛选 `id=fulltext` 关键字搜索 + "Filters" 面板；行首 checkbox `id=idscheckN`（value=page_id）；Action 列为 "Select" 下拉按钮（含 Edit/Delete/View），第一行 Edit 链接通常已展开可见。
- **CMS 页面编辑**：列表点行尾 Edit 进入 `/admin/cms/page/edit/page_id/N/`；Page Title 用 `input_text(index, text, clear=true)`（也支持 `send_keys(Control+v)` 粘贴）；保存点顶部 `id=save-button`，整页刷新回编辑页/列表。表单元素 id 每次加载随机（观察值 `id=YA7B21G` title、`id=BE9SXTU` is_active）——只能靠 `name=title`/`name=is_active` 定位。
- **新建购物车价格规则**：promo_quote 网格点 `id=add` → Rule Information 区 `name=name`/`description`/`is_active`/多选 `website_ids`/`customer_group_ids`/`coupon_type`/`uses_per_customer`/`from_date`/`to_date`/`sort_order`/`is_rss`。Actions 区（页面下方）下拉 `simple_action` + `discount_amount`。保存 `id=save`（成功整页跳回网格 "You saved the rule."）；`id=save_and_continue` 留在编辑页。网格行 tr title 含 `/edit/id/N/`。
- **创建发货**：详情页点 `id=order_ship` → Shipment 页：点 "Add Tracking Number" 插入行 `name=tracking[1][carrier_code]`（Custom Value/DHL/Federal Express/UPS/USPS）/`tracking[1][title]`/`tracking[1][number]`。非 custom 承运商自动填 Title。Items to Ship 各行 `name=shipment[items][N]` 默认已填。底部 "Submit Shipment" → 整页跳回详情 "The shipment has been created."
- **取消订单**：详情页 `id=order-view-cancel-button` → 确认框点 "OK"。
- **订单地址编辑**：详情页 Address Information → "Edit" → `id=street0/street1/city/postcode/telephone`、下拉 `id=country_id`/`id=region_id` → `id=save`。
- **新建商品**：`id=add_new_product` 选类型 → 先选 Attribute Set（点 "Default"）再填 `name=product[name]/[sku]/[price]/[quantity_and_stock_status][qty]/[...][is_in_stock]`。Categories："Select..." 勾树后 "Done"。`id=save-button` 成功 URL 变 `/edit/id/<新id>/`。
- 商品/CMS 编辑页文本用 `input_text(clear=true)`，下拉 `select_dropdown(index, value)`，布尔 checkbox 用 `click`，保存 `id=save-button`。
- 可配置变体：scroll 到 Configurations 区，`Edit Configurations` → `Next` → `Generate Products` → 重读 DOM。
- **主题页**（system_design_theme 阶段）：`Content` → `Themes` 进主题列表，点行尾 "View" 进主题详情（如 Theme: Magento Blank）；顶部 `id=back` 返回；标签 `id=theme_tabs_general_section`。
- 后台改商品后前台可能被 FPC 缓存——`System → Cache Management` 点 `id=flush_magento`。
- 结果确认消息："N records found"、"You saved the product./review./rule./page."、"The review has been deleted."、"You updated the order address."、"You canceled the order."、"The shipment has been created."；无结果 "We couldn't find any documents."
- 列排序点表头 th；长页 `scroll`；Shipment/规则 Actions 区等长表单 Submit 在页面底部需多次 scroll。
- `id=add`/`id=save`/`id=back` 跨页含义不同，按 title/上下文区分。
- 顶部 "System Messages" 通知区可能显示后台任务结果（如 "Task 'Trigger recollect totals...'"），非报错可忽略。