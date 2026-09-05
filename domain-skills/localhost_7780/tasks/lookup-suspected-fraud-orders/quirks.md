# Quirks — localhost

- **筛选面板元素 id 是随机生成的**（如 `id=CLS6LPS`、`id=XCDDRLO`），每次加载/刷新都会变；定位 Status 下拉必须靠 `name=status`，不要复用旧 id/index。
- **点击 Apply Filters 后网格是 AJAX 刷新，无页面跳转**（URL 保持 `/admin/sales/order/`）；需短暂等待后重新读 DOM，确认 "Active filters: Status: Suspected Fraud" 出现且 "records found" 数量更新，再读取结果。
- **筛选后 0 结果是合法结局**：本例中应用 Suspected Fraud 过滤后显示 "We couldn't find any records."（order_3 阶段）——不要误以为筛选失败而反复重试，应直接报告无此类订单。
- 侧边栏 `Sales` 菜单点击后需再点二级 `Orders` 链接才进入订单页；点击 Sales 后 DOM 会重渲染（menu 项 index 变化），需重新读 DOM 找 "Orders"。