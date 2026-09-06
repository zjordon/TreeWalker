# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Phone 过滤输入框 | Filters 展开面板中 "Phone" 标签下方 | `name=billing_telephone, type=text` | `id` 为随机串（每次加载不同），必须靠 name 定位；输入需去掉 +1 前缀 |
| Apply Filters 按钮 | 过滤面板底部 | `type=button, 可见文本"Apply Filters"` | 点击后整页刷新，需重新读 DOM |
| Filters 开关按钮 | 客户列表上方工具栏 | `可见文本"Filters"` | 过滤面板默认收起，先点它才出现各字段 |

其余元素（菜单项 Customers / All Customers、表格列等）在 sop_md 中已就地描述。