# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Stock Status 下拉 | 商品编辑页 Quantity 字段下方 | `name=product[quantity_and_stock_status][is_in_stock]`, 可见文本"In StockOut of Stock" | `id` 每次加载随机（如 QVH54E8/ENCP833），只能靠 name 定位 |
| 父商品 Edit 链接 | 搜索结果表格第一行 Action 列 | `aria-label=Edit Taurus Elements Shell`（无尺寸后缀） | 变体的 Edit 链接 aria-label 带如 "-XS-Yellow"，勿点错 |

其余元素（Catalog 菜单、fulltext 搜索框、Search 按钮、save-button、back）在 sop_md 中已唯一描述。