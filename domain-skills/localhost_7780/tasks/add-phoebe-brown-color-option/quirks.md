# Quirks — localhost

1. **Generate Products 按笛卡尔积生成**：Step 2 中勾选 Color=Brown 后（若 Size 全部保留选中），Generate Products 会为**所有尺码**各生成一个 brown 变体，而不是只有 S 码。录制即如此：最终 XS/S/M/L/XL 全部出现 Brown。若任务确实只要 S 码 brown，需在 Step 2 的 Size 属性值区只勾选 S（或在生成后对多余行使用行内 Select→Remove Product 删除）。录制证据未演示按尺码过滤，此点需在 Step 2 谨慎操作。
2. **新变体 price/qty/weight 必须手填**：矩阵新生成行的 price/qty/weight 输入框为空；不填直接 Save 不会报错，但保存后这些变体 price=$0.00、qty 为空（见 edit 阶段 Brown 行）。填写时参考同行其他变体（price 59.00 / qty 100 / weight 1）。
3. **向导步骤顺序**：必须依次点 "Next" 走完 3 步才出现 "Generate Products" 按钮；各步之间可能需要 scroll 才能看到 "Next"。
4. **保存为 AJAX 表单提交**：Save 后停留在同一编辑页（URL 加 `back/edit` 参数），成功标志是出现 "You saved the product." 文本；不是页面跳转。
5. **搜索结果包含子产品**：搜索 "Phoebe" 会同时返回 1 个 Configurable Product 和多个 `-S-Gray` 等后缀的 Simple Product；编辑入口必须选 Configurable 那一行，否则看不到 Configurations 区块。