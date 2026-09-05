# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 产品关键字搜索框 | 产品列表页顶部工具栏 | `id=fulltext, placeholder="Search by keyword"` | 需再点 `aria-label=Search` 按钮触发 |
| 可配置产品编辑入口 | 搜索结果中 Type 为 "Configurable Product" 的行，Action 列 | `aria-label="Edit Phoebe Zipper Sweatshirt"` | 与 16 个同名 Simple Product 的 Edit 链接区分，靠 aria-label 精确匹配不带后缀的产品名 |
| 配置向导入口 | 产品编辑页 Configurations 区块 | 可见文本 "Edit Configurations" | |
| 新变体矩阵输入框 | Generate Products 后 Current Variations 表格行内 | `name=configurable-matrix[N][price] / [qty] / [weight]` | 行按 Attributes 列文本（如 "Size: S, Color: Brown"）识别，N 为矩阵序号每次生成会变 |
| 保存按钮 | 页面右上角 | `id=save-button, 可见文本"Save"` | |