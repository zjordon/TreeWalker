# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 关键词搜索框 | 产品列表页顶部工具栏 | `id=fulltext, type=text, placeholder=Search by keyword` | 与全局搜索 `id=search-global` 区分 |
| Name 过滤输入框 | 点击 Filters 按钮后展开的过滤器面板内 | `name=name, type=text` | id 为随机串，靠 name 定位 |
| 库存数量输入框 | 商品编辑页表单中 Price 下方、Stock Status 上方 | `name=product[quantity_and_stock_status][qty]` | id 每次进编辑页都变（KDCWNJV/OMHDBNO/H5AOK4N/HBG07CA/B05RN2H），必须靠 name |
| 行内 Edit 链接 | 产品列表每行最后一列 Action | `aria-label=Edit <产品名>` | aria-label 含完整产品名可精确定位尺码 |