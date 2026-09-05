# Quirks — localhost

- **订单号格式**：筛选输入框填短号 "302"（increment_id），但页面标题显示为补零格式 "#000000302"。填 "302" 即可命中。
- **筛选面板的两个按钮**：筛选面板底部的 "Cancel" 按钮是关闭筛选面板，与取消订单无关（stage order_2）；取消订单的 Cancel 按钮在订单详情页（stage 302，`id=order-view-cancel-button`）。
- **确认弹窗**：点击 Cancel 后出现模态确认框，必须点击其中的 "OK"（stage 302_5）才真正取消；不点 OK 订单状态不变。
- **取消仅一次有效**：已取消后详情页顶栏 Cancel 按钮消失（stage 302 之后只剩 Back / Login as Customer / Reorder），不可重复取消。