# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 订单状态筛选下拉 | Filters 面板中，ID 输入框和 Bill-to Name 输入框之间，标签 "Status" | `name=status` | 选项含 Canceled/Closed/Complete/Suspected Fraud 等 12 项；另有 `name=store_id` 的 Purchase Point 下拉，勿混淆 |
| 清除筛选按钮 | 表格上方 "Active filters: Status: …" 区域内 | 可见文本"Clear all" | 仅在有残留 active filter 时出现 |

其余元素（Sales / Orders 菜单项、Filters / Apply Filters 按钮）在 DOM 中可通过可见文本唯一识别，已在 sop_md 中就地描述。