# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Quantity from 输入框 | Filters 面板中 "Quantity" 标签下第一个输入框 | `name=qty[from]`, `type=text` | input 的 `id` 是随机串（如 B6B20FH），每次加载不同，勿依赖 id |
| Quantity to 输入框 | Quantity 标签下第二个输入框 | `name=qty[to]`, `type=text` | 同上，id 随机（如 MAQHE6W） |

其余元素（Catalog/Products 菜单、Filters/Apply Filters 按钮、SKU 列）在 sop_md 中已就地描述，无歧义。