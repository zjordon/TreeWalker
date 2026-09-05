# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 产品价格输入框（编辑页） | 编辑页 Price 标签右侧、SKU 输入框下方 | `name=product[price], type=text` | 每次进入编辑页 id 都随机变化（如 S9AYEYL/HDP4NAC），必须按 name 定位 |
| 保存按钮（编辑页） | 编辑页顶部 Scope 选择器旁 | `id=save-button, 可见文本"Save"` | 保存为同步提交，页面短暂刷新 |
| 返回按钮（编辑页） | Save 按钮左侧 | `id=back, 可见文本"Back"` | 返回产品列表且保留筛选 |
| 名称筛选输入框（Filters 面板） | 点 Filters 展开后，Name 条件处 | `name=name, type=text` | id 随机（如 WCU4HXR） |
| 变体 Edit 链接 | 产品列表每行最后一列 | `aria-label="Edit <产品名>"`，如 `aria-label="Edit Hollister Backyard Sweatshirt-S-Green"` | 按 aria-label 区分不同尺寸变体 |