# Quirks — localhost

- **可配置商品的尺码变体是独立 Simple Product**：搜索商品名会返回可配置父商品 + 全部颜色/尺码变体（父商品 Quantity 为 0 且不可直接改库存）。必须用 Name 过滤器把颜色（如 `brown`）加进去，只改对应的 Simple Product 行（产品名后缀 `-<SIZE>-<Color>`）。
- **到货是"增加"而非"覆盖"**：Save 直接写入 qty 绝对值，需先从列表的 Salable Quantity / Quantity 列读出当前值，再加上到货数量（本例 100+378=478）。编辑页 qty 输入框 DOM 中不显示当前值。
- **每次保存后必须点 Back 返回列表再进入下一变体**：Save 后停留在当前编辑页；`button id=back` 返回列表后 Active filters（Keyword + Name）仍保留，列表仍是过滤后的 5 行，直接继续下一行即可。
- **编辑页 qty 输入框的 id 每次进入都随机变化**（如 KDCWNJV、OMHDBNO、H5AOK4N、HBG07CA、B05RN2H），不能跨页面复用 index/id；靠 `name=product[quantity_and_stock_status][qty]` 识别。
- **保存后列表的 Last Updated At / Quantity 列可能有缓存延迟**，不要据其判断保存是否成功；可信赖的是返回列表无报错且可继续操作。