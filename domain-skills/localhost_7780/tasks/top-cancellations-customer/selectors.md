# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Status 筛选下拉 | Filters 面板内，"Bill-to Name" 输入框之后 | `name=status` | `id` 与 `aria-label`（notice-XXX）每次加载随机，勿依赖 |
| Apply Filters 按钮 | 筛选面板底部，Cancel 按钮旁 | 可见文本"Apply Filters" | 点击后整页刷新 |
| 结果表格 Bill-to Name 列 | 表头 `Bill-to Name` 下的每行第 4 个 `<td>` | 可见文本即客户名 | 同名客户连续分组排列 |