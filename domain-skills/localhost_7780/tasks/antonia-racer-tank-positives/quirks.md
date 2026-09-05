# Quirks — localhost

- **列表页评论文本被截断**：Reviews 网格的 Review 列只显示约 40 字符 + "..."（如 stage nnsvenpa6mloayfq_3 中 "This is in regular rotation at the gym. Its col..."）。要读完整评论必须进入每条评论的 Edit Review 页读 `textarea id=detail`。
- **筛选状态保存在 URL 中**：按产品名搜索后 URL 变为 `.../index/filter/<base64>/...`（见 stage nnsvenpa6mloayfq_3 的滚动事件 URL）。从编辑页点 Back 返回后筛选仍然生效（stage product / 338 / 337 均只有 3 行），无需重新输入筛选条件。
- **该产品评论数固定为 3 条（ID 337/338/339）**，全部 Approved、同日期；其中 337 与 338 表面偏负面标题（"Zero support/modesty"、"Not for high impact"），339 偏正面——总结“喜欢的原因”时仍需通读三篇正文提取正面表述（时尚外观、颜色好看、适合瑜伽/低强度场景）。