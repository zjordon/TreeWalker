# 为 Phoebe Zipper Sweatshirt 的指定尺码新增 brown 颜色配置

Magento Admin (http://localhost:7780/admin) 中为可配置产品新增颜色变体。

## 步骤

1. **进入产品列表**：侧边栏点击 Catalog → Products，进入 `/admin/catalog/product/`。
2. **搜索产品**：在 keyword 搜索框 `input#fulltext placeholder="Search by keyword"` 输入 `Phoebe`，点击 `aria-label=Search` 按钮。结果第一条是 Configurable Product "Phoebe Zipper Sweatshirt"（ID 1130，SKU WH07）。注意：搜索结果还包含 16 个以 `Phoebe Zipper Sweatshirt-<size>-<color>` 命名的 Simple Product，必须编辑的是不带后缀的 Configurable Product 行（Type 列为 "Configurable Product"），点击其 `aria-label="Edit Phoebe Zipper Sweatshirt"` 的 Edit 链接。
3. **打开配置向导**：在产品编辑页向下滚动到 "Configurations" 区块，点击按钮 "Edit Configurations"。
4. **Step 1（选择属性）**：勾选 Size 与 Color 两个属性，点击 "Next"。
5. **Step 2（选择属性值）**：在 Color 值列表中勾选 `Brown`（录制中为 checkbox，如 `input type=checkbox`，无可见 label 文本，需在 Color 属性值区按顺序定位），点击 "Next"。⚠️ 见 quirks——若只想给 S 码加 brown，此处不能简单全选。
6. **Step 3（图片）**：可跳过，点击 "Next"。
7. **生成变体**：点击 "Generate Products"。矩阵 (configurable-matrix) 会列出所有新增 brown 变体行，每行有 name/sku/price/qty/weight 输入框（`name=configurable-matrix[N][price|qty|weight]`）。新行 price/qty/weight 为空，必须填写（参考现有变体：price=59.00，qty=100，weight=1.000000），否则保存后变体是 $0.00 / 无库存状态（见 edit 阶段 Brown 行除 XL 外 price 为 $0.00）。
8. **保存**：滚动到顶部，点击 `button id=save-button` "Save"。出现 "You saved the product." 提示即成功。保存后 URL 变为 `/admin/catalog/product/edit/id/1130/set/9/type/configurable/store/0/back/edit/`。

## 验证
保存后 "Current Variations" 表应出现 `Phoebe Zipper Sweatshirt-S-Brown`（SKU WH07-S-Brown）行。