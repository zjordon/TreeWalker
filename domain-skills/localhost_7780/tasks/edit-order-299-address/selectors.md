# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 地址编辑表单字段 | 地址编辑页 Order Address Information 表单内 | `id=street0` / `id=street1` / `id=city` / `id=postcode` / `id=region` / `id=region_id` / `id=country_id` | 两个地址（597/598）编辑页字段 id 相同，每次只出现一份表单 |
| 保存按钮 | 地址编辑页头部 | `id=save`, aria-label="Save Order Address" | 保存后自动返回订单详情页 |
| 订单号筛选框 | Orders 列表 Filters 展开区 ID 行 | `name=increment_id` | 输入 299 即可命中 000000299 |