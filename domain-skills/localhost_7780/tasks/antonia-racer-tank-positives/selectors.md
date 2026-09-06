# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 产品名筛选框 | Reviews 网格过滤行 Product 列 | `id=reviewGrid_filter_name, name=name, type=text` | 筛选值保留在 URL base64 参数中，Back 返回后仍生效 |
| 评论行 | 筛选后列表中的数据行 | `tr` 的 `title` 属性为 `.../admin/review/product/edit/id/<ID>/` | 点击整行或行内 Edit 链接均可进入编辑页 |
| 评论正文 | Edit Review 页 Review 字段 | `id=detail` 的 textarea | 列表页预览被截断，完整文本需进入编辑页 |
| 返回按钮 | Edit Review 页顶部按钮栏 | `id=back, 可见文本"Back"` | 回到筛选后的列表，不需重新筛选 |

其余元素（Search/Reset Filter 按钮、菜单项等）在 DOM 中有唯一 id 或明显可见文本，无需额外指纹。