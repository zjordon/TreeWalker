# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 评价列表 Product 筛选框 | Marketing>All Reviews 列表筛选行最后一列 | `id=reviewGrid_filter_name, name=name` | 输入产品名后点 Search |
| 评论全文 textarea | 评价编辑页 "Review" 字段 | `id=detail, name=detail` | 列表中正文被截断，全文只能在此读取 |
| 产品关键字搜索 | Catalog>Products 网格顶部 | `id=fulltext, placeholder=Search by keyword` | 搜索后结果中按行 aria-label 定位 Edit 链接 |
| 产品行 Edit 链接 | 搜索结果行最后一列 | `aria-label=Edit Antonia Racer Tank, 可见文本"Edit"` | 多产品时靠 aria-label 区分 |
| 保存按钮 | 产品编辑页右上 | `id=save-button, 可见文本"Save"` | 若被 tab 校验拦截，页面会出现红色提示 div |
| 刷新缓存按钮 | System>Cache Management 顶部 | `id=flush_magento, 可见文本"Flush Magento Cache"` | 保存描述后必须执行 |

其余元素在 sop_md 中已就地描述。