# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 订单号筛选输入框 | Orders 页筛选面板 "ID" 标签下 | `name=increment_id, type=text, maxlength=255` | 勿与 name=billing_name / shipping_name 混淆 |
| 取消订单按钮 | 订单详情页顶部按钮栏，Back/Edit 旁 | `id=order-view-cancel-button, title=Cancel, 可见文本"Cancel"` | 点击后弹确认框 |

其余元素（Sales/Orders 菜单、Filters、Apply Filters、View、OK）均已在 sop_md 中按可见文本就地描述，无歧义。