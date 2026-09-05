# 任务：将所有 Hollister 衬衫标记为 On Sale（勾选 Sale 属性）

前提：Magento 后台（http://localhost:7780/admin/），已登录 admin。

## 步骤

1. **进入产品列表**：点击左侧菜单 `Catalog`（`li id=menu-magento-catalog-catalog` 下的 `<a>`，可见文本 "Catalog"），再点击展开的 "Products" 链接，进入 `http://localhost:7780/admin/catalog/product/`。

2. **按关键词搜索**：先点击 "Clear all" 按钮清除已有筛选（若 "Active filters" 区域有残留筛选，否则可跳过）。然后在 `input id=fulltext placeholder="Search by keyword"` 中用 `input_text` 输入 `Hollister`，再点击 `button aria-label=Search`。结果会收敛到 16 条记录（1 页），包括 1 个 Configurable Product（如 "Hollister Backyard Sweatshirt"，ID 126）和多个 Simple Product 变体（如 -XS-Green 等，均为 "Not Visible Individually"）。

3. **逐个产品打开编辑页**：每个 Hollister 产品行末尾有 Edit 链接（`a aria-label="Edit <产品名>"`，可见文本 "Edit"），点击进入 `/admin/catalog/product/edit/id/<ID>/`。

4. **勾选 Sale 复选框**：在产品编辑页，Sale 属性是 `input type=checkbox name=product[sale]`（位于 "New" 复选框之后、字段标签 "Sale" 旁；证据中该产品 id=R3C3EHW，但 id 每次加载随机，务必靠 `name=product[sale]` 定位）。用 `scroll(amount, direction)` 向下滚动到该区域（约 3-6 屏，可用 scroll hint 判断），然后 `click` 该 checkbox（可先点其相邻 `<label>`，再点 checkbox 本身）。注意不要误勾旁边的 `product[new]`、`product[eco_collection]`、`product[performance_fabric]`、`product[erin_recommends]`。

5. **保存**：点击顶部 `button id=save-button title=Save`（可见文本 "Save"）。保存后页面短暂显示 "You saved the product." 成功提示。一次保存即可保存该产品；对 Configurable 产品，在父产品上勾选 Sale 即可，无需进入各变体（变体是 Not Visible Individually 的 Simple Product）。

6. **返回列表继续下一个**：点击 `button id=back title=Back`（可见文本 "Back"）回到产品列表（搜索筛选保留），重复步骤 3-5，直到所有 Hollister 产品均勾选了 Sale。

7. 完成后 `done(text, success)` 汇报已标记的产品数量。

## 注意
- 列表默认每页 20 条，Hollister 搜索结果 16 条单页即可看完（"records found" 会显示总数，若超过每页数量需翻页）。