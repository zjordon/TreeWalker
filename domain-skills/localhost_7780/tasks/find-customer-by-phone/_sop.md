# 按电话号码查找客户姓名和邮箱

目标：在 Magento 后台 (http://localhost:7780/admin/) 用电话号码（如 +1 2058812302）过滤客户列表，读取该客户的 Name 和 Email。

**Step 1: 进入 All Customers 页面**
- 从 Dashboard (http://localhost:7780/admin/admin/dashboard/) 点击左侧菜单 `Customers`（`a` 可见文本 "Customers"），展开子菜单。
- 点击子菜单项 `a` 可见文本 "All Customers"，进入 http://localhost:7780/admin/customer/index/。也可直接 `navigate("http://localhost:7780/admin/customer/index/")`。

**Step 2: 打开 Filters 面板**
- 点击按钮 可见文本 "Filters"（位于列表上方工具栏，与 "Default View" / "Columns" / "Export" / "Search by keyword" 同排）。
- 过滤面板展开后出现字段：Customer Since (from/to)、Name、Email、Group、Phone、ZIP、Country、State/Province，以及 "Cancel" 和 "Apply Filters" 按钮。

**Step 3: 填入电话号码**
- 定位 Phone 输入框：`input type=text name=billing_telephone maxlength=255`（注意：`id` 是随机串如 ETDWRK7，不要依赖 id）。用 `input_text(index=…, text="2058812302")`。
- 输入时去掉 "+1 " 前缀和空格，只输 10 位数字（本例 2058812302），过滤才能命中。

**Step 4: 应用过滤**
- 点击按钮 可见文本 "Apply Filters"。页面刷新为过滤结果：顶部显示 "Active filters: Phone: 2058812302"。

**Step 5: 读取结果**
- 结果表格列顺序：Name / Email / Group / Phone / ZIP / Country / State/Province / Customer Since。
- 本例结果：Name = John Smith，Email = john.smith.xyz@gmail.com（Phone 列 2058812302 可用于核对）。
- 完成后 `done(text, success=true)` 返回姓名和邮箱。