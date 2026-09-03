# Magento admin 已知路线（SOP）

## 批量改库存（Make all X out of stock）
1. 列表页关键词过滤出目标商品 → 勾选全部（表头 Select All + 逐行确认）。
2. **Actions > Update attributes**（不是 Change status——那只切启用/禁用）。
3. 打开 **Advanced Inventory** 页签 → 勾选 Stock Availability 的 Change 复选框
   → 下拉选 **Out of Stock**（Qty 可顺带 0）→ Save。
4. "Message is added to queue" 只是**异步队列确认**，库存未必立即生效——
   等待后回读编辑页 `select[name="product[quantity_and_stock_status][is_in_stock]"]`
   的值验证（0=Out of Stock）；未生效则逐个编辑页：JS 设值+change 事件+
   点 save-button。

## 价格规则（Cart Price Rule）
- 正确入口：`/admin/sales_rule/promo_quote/`（Marketing > Cart Price Rules）；
  新建表单 `/admin/sales_rule/promo_quote/new/`。
- 固定金额折扣的 `simple_action` 是 **`cart_fixed`**（百分比 `by_percent`；
  `by_fixed/by_percent` 是 **catalog** rule 的值域，写在 cart rule 里无效）。
- Actions 页签的 simple_action/discount_amount 字段可能隐藏在 DOM——可直接
  JS 设值；稳妥做法是**两段式**：先存基础信息（name/active/website/全部
  customer groups/No Coupon），保存后再补 Actions 字段二次保存。

## 报表类任务（"generate / show / create a report"）
- 在报表页（Reports > Sales > Orders 等）设置好参数（日期范围/周期）并点击
  **Show Report**，**页面呈现报表数据即为任务完成**——"generate a report" ≠
  "export a report"，不需要导出 CSV/PDF 文件。仅当 intent 明确说
  export/download 时才导出。
- Export 按钮点击常**无可见反馈**（属已知怪癖），导出失败不要反复重试——
  更不要转而自己从网格提取行拼 CSV（长表分块提取极耗步数，实测烧光预算）。

## 完成验证与提交流程
- 完成创建/修改类任务后，**停留在编辑页**回读关键字段值（name/价格/库存等）
  作为完成证据——不要导航去列表页"确认"（列表页视角丢失字段级证据）。
- 订单/出货/评论等**带副作用的操作优先走页面自身提交流程**（UI 提交会同步
  生成订单评论、通知记录等页面痕迹）；fetch POST 直发后端只用于**读数据**。
  若必须用 fetch POST 提交，事后到同页面的历史/评论区检查该操作是否留下了
  应有的记录（例：添加追踪号后 Comments History 应出现
  "Tracking number ... assigned"）。

## 评论检索（description 引用 / "why customers like" 类查询）
多级回退，**任一步落空不要直接下"无评论"结论**（评论可能存在但前台聚合索引缺失）：
1. storefront `fetch('/review/product/list/id/<product_id>/')` 解析 `.review-item`。
2. 若返回空（聚合索引缺失 `review_entity_summary.reviews_count=0` 时前台不渲染，
   即使评论真实存在）：**navigate 进 admin Reviews 网格**
   `/admin/review/product/index/`（必须浏览器导航——KO 网格行数据走 AJAX，
   fetch 页面 HTML 只得空壳），按产品 name/sku 筛选。
3. 从网格点进**评论编辑页** `/admin/review/product/edit/id/<id>/` 读全文——
   编辑页静态渲染全文完整（网格列表文本截断，但编辑页不截断）。
