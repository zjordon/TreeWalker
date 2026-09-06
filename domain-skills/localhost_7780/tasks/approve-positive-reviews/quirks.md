# Quirks — localhost

1. **过滤行的 select 是 grid filter，值格式为数字**：`reviewGrid_filter_status` 的 options 显示 `format=numeric`（Approved/Pending/Not Approved 对应 1/2/3）。用 `select_dropdown(index, "Pending")` 传可见文本即可，agent 内部会处理；但过滤后 URL 会带 base64 的 `filter/...` 段，之后 `Reset Filter` 按钮的 id 每次页面加载都变（如 `id_mFKTTdgYdlWUAtYqIDZjrRs2F1C2523Y` → `id_mzKEwUIAmPp0DBlcATAUWf1XlIZLheI3`），Search 按钮同理——定位这些按钮要靠 `aria-label`/title，不要记 id。
2. **保存成功信号**：点 "Save Review" 后是整页跳转回 Reviews 列表（非 AJAX toast），页头出现文本 "You saved the review."；以该文本确认保存，不要在编辑页重复点击。
3. **情感判断需人工读内容**：任务要求只批准正面评价。列表 Title/Review 列可判断（如 "Quite good" vs "Bad!"）；本例中 353（Bad!）和 351（won't recommand）为负面，未批准；352/349/347 已批准。
4. **批准后该行从 Pending 过滤结果中消失**：保存返回列表后，若过滤仍生效，已批准的行不再出现，剩余行即待处理项；不要因行数减少误判为失败。