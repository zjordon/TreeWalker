# Selectors — localhost

所有关键元素（`name=name`、`name=website_ids`、`name=customer_group_ids`、`name=simple_action`、`name=discount_amount`、`id=add`、`id=save`）在 sop_md 中已用 `name`/`id` 属性唯一描述。注意：本站表单字段的 `id` 是随机生成的（如 id=XWK6F5T、id=UB0EPE5），**不要依赖 id，必须用 `name` 属性定位**。

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| Actions 折叠区块 | New Cart Price Rule 页中部，可见文本 "Actions"（带未保存提示文字） | 可见文本"Actions" | 无 id/name；需 click 展开后才能看到 simple_action / discount_amount 字段 |