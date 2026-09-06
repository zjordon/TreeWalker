# 修改订单 #299 的地址（Magento Admin）

目标：把订单 #000000299 的收货地址改为 456 Oak Avenue, Apartment 5B, New York, NY, 10001。

## 步骤

1. **进入订单列表**：在后台左侧菜单 click `Sales` → `Orders`（可见文本 "Orders"）。
2. **按订单号筛选**：click `Filters` 按钮（可见文本 "Filters"），在 ID 筛选框 `input[name=increment_id]`（ID 列下的文本框）中输入订单号（注意：用户说的 "order #299" 对应 increment_id `000000299`，输入 299 或 000000299 均可命中）。如之前有旧筛选条件，先点 "Remove" 清除。然后 click `Apply Filters`。
3. **打开订单**：click 结果行中的 `View` 链接，进入订单详情页（URL: `/admin/sales/order/view/order_id/299/`）。
4. **分别编辑两个地址**：订单页有 Billing Address 和 Shipping Address 两个区块，各有自己的 "Edit" 链接（URL 分别为 `/admin/sales/order/address/address_id/598/`（billing）和 `/address_id/597/`（shipping））。**地址修改需对两个地址各做一次**：click 一个地址区块旁的 `Edit` 链接进入地址编辑表单（form `id=edit_form`）。
5. **填写地址字段**（均以 id 定位，页面滚动后可见）：
   - `input#street0`（name=`street[0]`）→ `456 Oak Avenue`
   - `input#street1`（name=`street[1]`）→ `Apartment 5B`
   - `input#city` → `New York`
   - `input#region`（文本框）或 `select#region_id` → `New York`（US 州字段；region_id 下拉可用 select_dropdown 直接选）
   - `input#postcode` → `10001`
   - Country 保持/选择 `United States`（`select#country_id`，用 select_dropdown）
6. **保存**：click `button id=save`（aria-label="Save Order Address"，可见文本 "Save Order Address"）。保存后返回订单详情页，出现 "You updated the order address." 提示。
7. **对另一个地址重复步骤 4–6**。
8. 完成后在订单详情页核对 Address Information 中两个地址均显示新地址，然后 `done`。