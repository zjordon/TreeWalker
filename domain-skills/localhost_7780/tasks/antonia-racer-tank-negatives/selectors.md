# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 产品名筛选输入框 | Reviews 列表页筛选行 "Product" 列 | `id=reviewGrid_filter_name, name=name, type=text` | 筛选行最后一个输入列，与 SKU 筛选框相邻 |
| 搜索按钮 | 列表页左上工具栏，New Review 旁 | `aria-label=Search, 可见文本"Search"` | 提交筛选条件 |
| 重置筛选按钮 | 搜索按钮旁 | `aria-label=Reset Filter, 可见文本"Reset Filter"` | id 为随机串，勿依赖 id |
| 评论全文 textarea | Edit Review 页 "Review" 字段 | `id=detail, name=detail` | 全文在此字段中 |
| 返回列表按钮 | Edit Review 页顶部 | `id=back, aria-label=Back, 可见文本"Back"` | 回到筛选后的列表 |

其余元素（侧边栏 Marketing/All Reviews 链接、评论行 tr）均已在 sop_md 中就地描述。