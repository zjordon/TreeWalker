# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Product 列过滤输入框 | Reviews 表格过滤行，Type 下拉之后、SKU 过滤框之前 | `id=reviewGrid_filter_name, name=name, type=text` | 切勿误用 `reviewGrid_filter_sku`（SKU 过滤框）或 `reviewGrid_filter_title` |
| Search 按钮 | 过滤表格上方工具栏，New Review 与 Reset Filter 之间 | `title=Search, type=button, aria-label=Search` | 触发整页刷新（非 AJAX） |
| 评论行 Edit 链接 | 每行最后一列 | 可见文本"Edit"；所在 `tr` 的 title 含 `/admin/review/product/edit/id/<ID>/` | 唯一进入能看到邮箱的入口 |

其余元素（Marketing 菜单、All Reviews 链接等）在 sop_md 中已就地描述。