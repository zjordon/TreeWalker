# Quirks — localhost

1. **列表正文截断**：评价列表 Review 列显示 "This is in regular rotation at the gym. Its col..."，取引用全文必须点 Edit 进入 `/admin/review/product/edit/id/<id>/` 读 `textarea#detail`（stage 339_4 / 338_6）。
2. **产品编辑页 Content tab 校验拦截**：stage 1796_10 中 Content 标签出现 "This tab contains invalid data. Please resolve this before saving." 提示时，直接点 Save 不会成功——需先进入 Content 区处理无效字段。保存成功后 URL 会变为 `.../edit/id/1796/set/9/type/configurable/store/0/back/edit/` 并停留编辑页（非跳转列表）。
3. **Short Description 是 TinyMCE iframe**（stage 1796_10：`iframe title="Rich Text Area" id=product_form_short_description_ifr`），不是普通 textarea；正文段落在其内部 `<p>` 节点中。Description（Details 标签的正文）是另一个字段，勿混淆——前台 PDP 顶部引用文字来自 Short Description。
4. **保存后必须 Flush Magento Cache**（System > Cache Management > Flush Magento Cache），否则前台 `http://localhost:7780/antonia-racer-tank.html` 继续显示缓存的旧描述（stage cache 证明先 flush 后前台才更新）。
5. **产品编辑页极长**：stage 1796 的可滚动区块提示 total 3.3–6.0 pages；Content/Short Description 在页面较深位置，需多次 `scroll(amount, direction)` 下移后再找 Content 区。
6. **评价筛选入口**：All Reviews 列表默认 351 条且首页没有 Antonia 的评价，必须用 `reviewGrid_filter_name` 筛选（也可用 SKU WT08 的 `reviewGrid_filter_sku`）。