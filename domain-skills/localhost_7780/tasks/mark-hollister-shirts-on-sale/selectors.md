# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Sale 复选框 | 产品编辑页属性区，"Sale" 标签旁 | `name=product[sale], type=checkbox` | id（如 R3C3EHW）每次页面加载随机变化，不要依赖 id |
| 关键词搜索框 | 产品列表 "Filters" 按钮右侧 | `id=fulltext, placeholder="Search by keyword"` | 输入后需点击 aria-label=Search 按钮触发 |
| 产品行 Edit 链接 | 产品列表每行最后一列 | `aria-label="Edit <产品名>"， 可见文本"Edit"` | aria-label 包含完整产品名，可精确区分行 |