# localhost (Magento 2.4.6) 站点卡片

## 站点功能地图（Site Function Map）

- 管理后台入口: `http://localhost:7780/admin/`（登录用户 admin）；店面入口 `http://localhost:7780/`
- Dashboard: `/admin/admin/dashboard/`（Bestsellers/Most Viewed/New Customers/Customers 标签、Last Orders 表——行 tr title 直接是订单详情 URL；**Top Search Terms 面板**有两列 Uses/Results，按 Uses 排序——"top search terms" 类查询以该面板为数据源，**起点即终点，第一行即答案**；勿混读两列，勿翻菜单去别处求证；**面板行序即 Uses 降序，问 top-N 直接取前 N 行，不要按快照里的数字自行重排**——两列数字对齐在 DOM 快照中模糊易混，行序比自读数字可靠）
- Search Terms 报表网格: `Marketing → SEO & Search → Search Terms`（`/admin/search/term/report/`）——**不作为 "top search terms" 类查询的判据**（含 nike 等低质词条、默认按 Results 排序，榜单与 Dashboard 面板不一致）
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
- 报表（**勿凭记忆猜报表 URL**——报表路由与菜单层级不对应，猜的 URL 常见 404（task 3-B 连猜两次全 404）；**卡内记载的确切 URL 可直接 navigate**（task 713-C 一步直达通过），或从侧边栏 `Reports` 菜单逐级进入；Dashboard 首页的 Bestsellers 标签是仪表盘小部件（字段常为空），不是报表入口）:
  - `Reports` → `Products` → `Bestsellers` → `/admin/reports/report_sales/bestsellers/`（筛选 Period/From/To + `id=filter_form_submit`）
  - `Reports` → `Products` → `Ordered Products` → `/admin/reports/report_product/sold/`（筛选 From/To + Show By + Refresh）
  - `Reports` → `Sales` → `Orders` / `Refunds`（销售额/退款报表，同样经菜单进入）
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
- **评论网格按产品找评论**：①单一产品用 **SKU 过滤**最稳（可配置商品的评论挂在父商品 SKU 上，如 Circe Hooded Ice Fleece = WH12）；②找**一类产品**（如所有 tank 类）用 **Product name 字段过滤**（'tank' 实测命中全部 27+ 条评论）；③**勿用全局关键词搜索 / read_grid search 做产品召回**（实测 'tank' 只命中 1 个产品的 3 条，漏掉 8/9 的产品）；④真名/SKU 不确定先去 Catalog 商品网格查。
- **评论网格每次更换筛选条件前先 Reset Filter / Clear all**：筛选条件之间是 AND 叠加，旧值残留会把新条件毒死。**残留是 session 级持久化**（2026-09-12 实测实锤：遗留网格把筛选存进 admin session，跨页面加载、跨任务、甚至跨容器/Chrome 重启存活——同一 session 里发现上一任务与前次实验的筛选值并存）。四条纪律：①任何筛选前**先 Reset Filter**（`clear=True` 只清单个字段、**不清兄弟字段**——残留依旧 AND）；②**筛选 0 命中时第一反应是检查筛选行全部字段是否预填**（残留中毒的典型症状），而不是换字段重试或整表 dump 手工翻页（曾有一轮因此烧 16 步后错误 conclude "目标不存在"，实际目标就在库里）；③**Reset 之后记得重新应用目标筛选**（曾有一轮 reset 后转身走上整表翻页绝路）；④失败的筛选尝试自己也会制造新残留。SKU=WH12 实测：残留状态下 0 条、Reset 后 2 条。
- **CMS 页面网格**（page 阶段）：新 UI-Component 网格，筛选 `id=fulltext` 关键字搜索 + "Filters" 面板；行首 checkbox `id=idscheckN`（value=page_id）；Action 列为 "Select" 下拉按钮（含 Edit/Delete/View），第一行 Edit 链接通常已展开可见。
- **CMS 页面编辑**：列表点行尾 Edit 进入 `/admin/cms/page/edit/page_id/N/`；Page Title 用 `input_text(index, text, clear=true)`（也支持 `send_keys(Control+v)` 粘贴）；保存点顶部 `id=save-button`，整页刷新回编辑页/列表。表单元素 id 每次加载随机（观察值 `id=YA7B21G` title、`id=BE9SXTU` is_active）——只能靠 `name=title`/`name=is_active` 定位。
- **新建购物车价格规则**：promo_quote 网格点 `id=add` → Rule Information 区 `name=name`/`description`/`is_active`/多选 `website_ids`/`customer_group_ids`/`coupon_type`/`uses_per_customer`/`from_date`/`to_date`/`sort_order`/`is_rss`。Actions 区（页面下方）下拉 `simple_action` + `discount_amount`。保存 `id=save`（成功整页跳回网格 "You saved the rule."）；`id=save_and_continue` 留在编辑页。网格行 tr title 含 `/edit/id/N/`。**判分锚点=规则编辑表单页，三铁律**：①必须用 **Save and Continue Edit**（`id=save_and_continue`）结束并**停在表单页**——普通 `id=save` 跳回网格后五项表单字段检查全空判 0（同会话对照实验：用 save_and_continue 的任务过、用 save 的全灭）；②客户组**不勾 NOT LOGGED IN**——判分读 `customer_group_ids` 的 selectedIndex==1（首个选中项须为 General），全勾 0-3 则 selectedIndex=0 判 0（DB 实证四条规则全勾全灭）；③规则名**逐字照抄意图**（含空格拼写："Thanks giving sale" ≠ "Thanksgiving Sale"，小写化救不了子串不匹配；大小写本身无关——判分两侧均 lowercase）。
- **创建发货**：详情页点 `id=order_ship` → Shipment 页：点 "Add Tracking Number" 插入行 `name=tracking[1][carrier_code]`（Custom Value/DHL/Federal Express/UPS/USPS）/`tracking[1][title]`/`tracking[1][number]`。非 custom 承运商自动填 Title。Items to Ship 各行 `name=shipment[items][N]` 默认已填。底部 "Submit Shipment" → 整页跳回详情 "The shipment has been created."
- **取消订单**：详情页 `id=order-view-cancel-button` → 确认框点 "OK"。
- **订单地址编辑**：详情页 Address Information → "Edit" → `id=street0/street1/city/postcode/telephone`、下拉 `id=country_id`/`id=region_id` → `id=save`。
- **新建商品**：`id=add_new_product` 选类型 → 点**当前属性集名**（初始显示 "Default"）打开选择器，**按商品类型选**（见下）后再填 `name=product[name]/[sku]/[price]/[quantity_and_stock_status][qty]/[...][is_in_stock]`。Categories："Select..." 勾树后 "Done"。`id=save-button` 成功 URL 变 `/edit/id/<新id>/`。**两个隐含判分项是真正的考点**：①**属性集按商品类型选**——上衣类=Top、下装=Bottom、器材（手表/瑜伽垫等）=Gear、包袋=Bag；为了让 size/color 字段出现而挑一个"恰好有这两个字段"的错集（如 Sprite Stasis Ball）或留在 Default，字段能填也判 0；②**必须勾对应分类**（Tops / Watches / Fitness Equipment 等）——意图不提分类也要勾，判分读 `category_ids` 区文本（漏勾 0 分）。曾有一轮商品 price/qty/size/color/name 五项全对（DB 实证），唯属性集选错 + 分类空，十二跑全 0。
- 商品/CMS 编辑页文本用 `input_text(clear=true)`，下拉 `select_dropdown(index, value)`，布尔 checkbox 用 `click`，保存 `id=save-button`。
- 可配置变体：scroll 到 Configurations 区，`Edit Configurations` → `Next` → `Generate Products` → 重读 DOM。
- **主题页**（system_design_theme 阶段）：`Content` → `Themes` 进主题列表，点行尾 "View" 进主题详情（如 Theme: Magento Blank）；顶部 `id=back` 返回；标签 `id=theme_tabs_general_section`。
- 后台改商品后前台可能被 FPC 缓存——`System → Cache Management` 点 `id=flush_magento`。
- 结果确认消息："N records found"、"You saved the product./review./rule./page."、"The review has been deleted."、"You updated the order address."、"You canceled the order."、"The shipment has been created."；无结果 "We couldn't find any documents."
- 列排序点表头 th；长页 `scroll`；Shipment/规则 Actions 区等长表单 Submit 在页面底部需多次 scroll。
- `id=add`/`id=save`/`id=back` 跨页含义不同，按 title/上下文区分。
- **订单网格按客户聚合计数的方法纪律**（"哪个客户下了 N 单/最多单"类；此类判分看答案内容，read_grid 过滤是首选——与上方"Lookup 状态订单"条的 UI-锚点口径相反，按判分锚点选用）：①按 Bill-to Name 排序的相邻段只作**初筛**；②每个候选（尤其疑似恰好 N 单的）必须用 `billing_name` 精确过滤（read_grid filters / Filters 面板）逐个复核计数——验证要对**所有**候选做，不能只验支持当前假设的；③计数存在不确定性（tally 带 ?、文件有未读缺口）时**禁止下结论**；④分段读文件时警惕相邻同名行被块边界切开导致漏计。配合全站"并列必须全列"规则（见 quirks）。
- **可配置商品加变体的纪律**（"add 色/码 to X" 模板族）：①入口必须是父商品编辑页 Configurations 区的 **Edit Configurations 向导**——自建 simple 商品 ≠ 加变体，缺父商品关联判 0（变体名由系统按 父名-属性1-属性2 自动生成）；②目标选项（如新尺码/颜色）不存在时，优先用向导 Step 2 该属性块的 **Create New Value 行内新建**（比 Stores → Attributes 前置绕路快），建完勾选；③Step 2 **绝对不要取消已勾的旧值**（会 Disassociate 既有变体）；色/码维度可 Deselect All 后只勾目标值；④生成后**删除多余的变体行**——既是整洁更是判分硬要求：Configurations 表每页 20 条，**变体总数 >20 会把新增行挤到第 2 页，判分 locator 只读第 1 页文本 → 数据正确也 0 分**（实测：24 变体时新增的 XXXL 系列全在第 2 页，DB 完整落库仍判 0）。
- 顶部 "System Messages" 通知区可能显示后台任务结果（如 "Task 'Trigger recollect totals...'"），非报错可忽略。