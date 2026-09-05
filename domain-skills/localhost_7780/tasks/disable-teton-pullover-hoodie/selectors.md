# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 关键词搜索框 | 产品列表网格上方过滤器栏 | `id=fulltext, placeholder="Search by keyword", aria-label="Search by keyword"` | 搜索前若有 Active filters 需先 Clear all |
| 父产品 Edit 链接 | 搜索结果中 Configurable Product / SKU=MH02 那行的 Action 列 | `aria-label="Edit Teton Pullover Hoodie"`，可见文本"Edit" | 变体行的 aria-label 带 -尺码-颜色 后缀，勿点错 |
| 启用状态复选框 | 编辑页表单顶部 "Enable Product" 标签旁 | `name="product[status]", type=checkbox, value=2` | id（如 BQG6PKS）每次加载会变，勿依赖 |
| 保存按钮 | 编辑页右上角 | `id=save-button, title=Save, aria-label=Save`，可见文本"Save" | |
| 返回按钮 | 编辑页 Save 旁 | `id=back, title=Back`，可见文本"Back" | |