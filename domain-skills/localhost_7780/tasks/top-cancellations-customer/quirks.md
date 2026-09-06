# Quirks — localhost

- **筛选结果单页懒加载**（order_3 阶段）：应用 Status=Canceled 后虽显示 142 条记录，翻页按钮 disabled，但表格行随滚动逐步渲染。必须反复 `scroll` 直到无新行再统计，否则计数不完整。
- **表格按客户分组排序**（order/order_3 阶段）：取消订单列表天然按 Bill-to Name 分组（同名连续出现），统计时可直接按组计数，无需逐行累计。
- **下拉的 id/aria-label 是随机串**（order_2 阶段）：`<select name=status id=KDI9BV6 aria-label=notice-KDI9BV6>` 的 id 与 aria-label 每次页面加载都会变，只能靠 `name=status` + 面板上下文识别；`select_dropdown(index, "Canceled")` 一次完成，无需先点击。
- **Apply Filters 触发整页刷新**：应用筛选后页面重新加载，之前的索引全部失效，需重新读取 DOM。