# 任务：统计最近 2 笔订单售出的总件数（Magento 后台 localhost:7780/admin）

## 思路
最近 2 笔订单可以直接从 Dashboard 的 "Last Orders" 面板读出（订单按新到旧排列），然后逐个打开订单详情页，把 "Items Ordered" 表中每个商品的 Qty 相加。

## 步骤

### 步骤 1：从 Dashboard 识别最近 2 笔订单
在 `http://localhost:7780/admin/admin/dashboard/` 的 "Last Orders" 面板中，每行 `<tr>` 的 `title` 属性即为订单详情 URL，如 `http://localhost:7780/admin/sales/order/view/order_id/299/`（Sarah Miller，$194.40）和 `.../order_id/65/`（Grace Nguyen，$190.00）。也可点击顶部菜单 Sales → Orders 进入订单列表。

### 步骤 2：打开订单 1 详情
导航到 `http://localhost:7780/admin/sales/order/view/order_id/299/`（或点击该行 / 列表行的 View 链接）。页面顶部显示 `#000000299`。

### 步骤 3：读取 Items Ordered 的 Qty
向下 `scroll`（每次 2-5 个视口）直到出现 "Items Ordered" 表。每个商品行：
- `<td>` 内含商品名 + `SKU:` + Color/Size
- Qty 列在 "Price" 列之后的 `<td>` 内，数量位于嵌套 `<tr>` 的 "Ordered" 下的 `<td>` 中（如 [39172]）

订单 299（May 31, 2023）：Argus All-Weather Tank、Marco Lightweight Active Hoodie、Tiffany Fitness Tee、Nadia Elements Shell、Gwen Drawstring Bike Short —— 各 1 件，共 5 件。

### 步骤 4：返回并打开订单 2 详情
点击 `<button id=back aria-label=Back>Back</button>` 返回订单列表，再点击第二行的 `View` 链接（文本 "View"，`data-tw-jsclick=1`），或直接 `navigate` 到 `.../order_id/65/`。页面顶部显示 `#000000065`。

### 步骤 5：读取订单 2 的 Qty
同样滚动到 "Items Ordered"。订单 65（May 28, 2023，Grace Nguyen）：Neve Studio Dance Jacket、Minerva LumaTech™ V-Tee、Frankie Sweatshirt、Sparta Gym Tank —— 各 1 件，共 4 件。

### 步骤 6：汇总并完成
总件数 = 5 + 4 = 9。调用 `done(text="9", success=true)`。