# 任务：下架（禁用）Teton Pullover Hoodie

Magento Admin（http://localhost:7780/admin/）流程：

## 步骤 1：进入产品列表
从 Dashboard 侧边栏点击 `Catalog`（li id=menu-magento-catalog-catalog 下的 `<a>Catalog</a>`），再点击展开菜单中的 `Products`，进入 http://localhost:7780/admin/catalog/product/。

## 步骤 2：搜索产品
在产品网格上方找到 `input#fulltext`（placeholder="Search by keyword"，aria-label 同名）：
1. 若有残留的 Active filters（页面显示 "Active filters: Keyword: ..."），先点击 `Clear all` 按钮清除。
2. `input_text(index, "Teton Pullover Hoodie", clear=true)` 输入关键词。
3. 点击 `button[aria-label=Search]` 执行搜索。结果应为 16 条记录（父 configurable product + 各尺码/颜色 simple product 变体）。

## 步骤 3：打开父产品编辑页
目标行是 Type 为 "Configurable Product"、SKU 为 MH02、Name 精确等于 "Teton Pullover Hoodie"（不带 -XS-Red 等后缀）的行。点击该行的 `a[aria-label="Edit Teton Pullover Hoodie"]`（可见文本 "Edit"）。不要点变体（Simple Product）的 Edit。

## 步骤 4：取消勾选 Enable Product
进入编辑页后（标题显示 "Teton Pullover Hoodie"），表单顶部有 "Enable Product" 复选框：`input[name="product[status]"] type=checkbox`。当前若为 checked=true（启用），点击它或其相邻 `<label>` 取消勾选——**取消勾选后 checked=false、value=2，即 Disabled 状态**。只需操作父产品的这一个开关，无需逐个禁用变体。

## 步骤 5：保存
点击 `button#save-button`（title=Save / aria-label=Save）。保存后点击 `button#back`（title=Back）返回产品列表。

## 步骤 6：验证
在产品列表中确认 "Teton Pullover Hoodie"（Configurable 那行）的 Status 列显示 "Disabled"，然后 `done(text, success=true)`。