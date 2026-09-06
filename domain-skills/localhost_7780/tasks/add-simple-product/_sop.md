# 新增 Simple Product（Energy-Bulk Women Shirt）

1. 从 Dashboard 进入：点击左侧菜单 `Catalog` → `Products`，进入商品列表页。
2. 点击右上角 `div id=add_new_product aria-label=Add Product`（可见文本 "Add Product Select Simple Product ..."），在弹出菜单中选 `Simple Product`；也可直接导航到 `http://localhost:7780/admin/catalog/product/new/set/4/type/simple/`。
3. 新建页初始 Attribute Set 为 "Default"，但 Size/Color 属性只在 `Top` 属性集下存在。点击 Attribute Set 下拉（可见文本 "Default"），在展开列表中点击 `div` 可见文本 "Top"（列表含 Bag/Bottom/Default/Downloadable/Gear/Sprite Stasis Ball/Sprite Yoga Strap/Top）。**必须先选 Top，再填其他字段**，否则表单字段会刷新重排。
4. 填写基础字段（均在页面顶部区域）：
   - Product Name：`input name=product[name]`（示例任务填 "Energy-Bulk Women Shirt"）。
   - Price：`input name=product[price]`（填 60）。
   - Quantity：`input name=product[quantity_and_stock_status][qty]`（填 50）。Stock Status 保持 "In Stock"。
5. Size 与 Color 下拉在页面下方（需 `scroll` 3-7 屏）：
   - Size：`select name=product[size]`，选项含 55 cm/XS/65 cm/S/75 cm/M/... ，用 `select_dropdown(index, "S")` 选中。
   - Color：`select name=product[color]`，选项 Black/Blue/Brown/.../Yellow，用 `select_dropdown(index, "Blue")` 选中。
6. 点击右上 `button id=save-button`（可见文本 "Save"）保存。保存成功后页面跳转到 `/admin/catalog/product/edit/id/<新id>/...` 的编辑页（URL 变化即为成功标志），然后 `done`。