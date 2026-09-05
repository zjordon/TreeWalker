# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 订单筛选 ID 输入框 | Orders 列表页 Filters 面板内 | `name=increment_id` | Filters 面板展开后才出现 |
| 承运商下拉 | New Shipment 页跟踪号表格，点击 Add Tracking Number 后新增行 | `name=tracking[1][carrier_code]` | 行号随添加次数递增，动态 id 如 trackingC1 |
| 跟踪号输入框 | 同上新增行的第三列 | `name=tracking[1][number]` | 必填 |
| 跟踪号标题输入框 | 同上新增行的第二列 | `name=tracking[1][title]` | 选 fedex 后自动填充，无需手动输入 |

其余元素（Sales/Orders 菜单、Filters、Apply Filters、View、Ship、Add Tracking Number、Submit Shipment 按钮）均有稳定 title/可见文本，已在 sop_md 中描述。