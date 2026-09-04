# Selectors — localhost

多数元素已在 sop_md 用稳定 id/name 描述。需指纹的补充：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 网格行选中 checkbox | 每行首列 | `type=checkbox`, id 形如 `idscheckN` | id/value 即实体 id |
| 行 Edit/View 链接 | 行尾 Action 列 | 可见文本"Edit"/"View"或行 `tr title=.../edit/id/N/` | 订单网格只有 View；主题网格行尾也是 "View"；CMS 网格 Action 列是 "Select" 按钮展开 Edit/Delete/View，首行 Edit 可能已直接可见 |
| CMS 页面编辑表单字段 | /admin/cms/page/edit/ 页 Page Title/Enable Page 区 | `name=title`、`name=is_active` | 输入元素 id 每次加载随机，只能靠 name |
| 订单地址 Edit 入口 | 订单详情页 Billing/Shipping 地址块内 | 可见文本"Edit" | billing/shipping 各一个，URL 含不同 address_id |
| 评论筛选输入 | 评论网格筛选行 | `id=reviewGrid_filter_name`/`_sku`/`_title`/`_nickname`/`_detail`/`_review_id`/`_status`/`_type` | id 稳定 |
| 促销规则筛选输入 | promo_quote 网格筛选行 | `id=promo_quote_grid_filter_rule_id`/`_name`/`_coupon_code`/`_sort_order`；日期 `name=from_date[from]`/`from_date[to]`/`to_date[from]`/`to_date[to]` | 日期输入 id 含随机串，靠 name/placeholder=mm/dd/yyyy |
| 促销规则筛选下拉 | promo_quote 网格筛选行 | `id=promo_quote_grid_filter_is_active`（Active/Inactive）/`id=promo_quote_grid_filter_rule_website` | id 稳定 |
| 规则表单字段（新建/编辑页） | Rule Information 区 | `name=name`/`description`/`is_active`/`website_ids`/`customer_group_ids`/`coupon_type`/`uses_per_customer`/`from_date`/`to_date`/`sort_order`/`is_rss` | 输入元素 id 每次加载随机，只能靠 name |
| Actions 区折扣字段 | 规则表单 Actions 标签区（页面下方） | `name=simple_action`、`name=discount_amount` | simple_action 选项含 "Percent of product price discount" 等 |
| 规则保存按钮 | 规则表单页顶部 | `id=save`、`id=save_and_continue`、`id=back`、`id=reset`、`id=delete` | 编辑页有 Delete，新建页无 |
| 订单筛选输入 | Orders Filters 面板 | `name=increment_id`/`billing_name`/`shipping_name`/`created_at[from]`/`base_grand_total[from]` 等 | id 随机靠 name |
| 筛选清除按钮 | 应用筛选后网格上方 Active filters 区 | 可见文本 "Clear all" | 一次清除全部激活筛选 |
| 订单地址表单字段 | /sales/order/address/ 页 | `id=street0`/`street1`/`city`/`postcode`/`telephone`；下拉 `id=country_id`/`id=region_id` | country 切换后 region_id 可能被隐藏替换为 `id=region` |
| 追踪号行控件 | 新建 Shipment 页，点 "Add Tracking Number" 后出现 | `name=tracking[1][carrier_code]`/`tracking[1][title]`/`tracking[1][number]` | 点之前 DOM 中不存在该行 |
| 发货数量输入 | 新建 Shipment 页 Items to Ship 表各行 | `name=shipment[items][N]` | 默认已填订购量 |
| 缓存刷新按钮 | Cache Management 顶部 | `id=flush_magento`/`flush_system` | |
| Attribute Set 选择入口 | 新建商品页 | 可见文本"Default" | 点击后出现列表 + 搜索框 |
| Categories 选择入口 | Categories 区 | 可见文本"Select..."；树内 checkbox + "Done" | 勾选后必须点 Done |
| 变体矩阵输入 | Current Variations 表各行 | `name=configurable-matrix[N][price]` 等 | 另有 type=file 图片输入勿混淆 |
| Dashboard 订单行 | Last Orders 表 | 行 `tr title=.../sales/order/view/order_id/N/` | 直接 navigate 行 URL 可跳过筛选 |
| 网格每页条数/页码 | 旧网格底部分页条 | `id=promo_quote_grid_page-limit`（20/30/50/100）、`id=promo_quote_grid_page-current` | 其他旧网格 id 前缀同理 `<grid>_page-*` |
| 主题 General 标签 | 主题详情页（Theme: Magento Blank 等） | `id=theme_tabs_general_section`、`name=general_section`，可见文本"General" | 主题详情顶部 Back 为 `id=back` |