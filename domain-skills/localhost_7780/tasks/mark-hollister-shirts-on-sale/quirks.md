# Quirks — localhost

- **Sale 复选框的 id 不稳定**：stage 126 中 `input name=product[sale]` 的 id 为 `R3C3EHW`，但此类 id 每次页面加载随机生成，只能靠 `name=product[sale]` 定位（相邻的 `product[new]`、`product[eco_collection]` 等同类 checkbox 也是随机 id，勿误勾）。
- **搜索前需 Clear all**（stage product）：产品列表若残留上一次的关键词筛选（页面显示 "Active filters: Keyword: ..."），搜索框行为可能异常；先点 "Clear all" 再输入关键词。
- **保存是同步整页保存**（stage 126_5）：点击 Save 后页面显示 "You saved the product." 提示；提示短暂，若未看到提示可再读 DOM 确认，不要重复点击 Save。
- **Back 按钮保留搜索筛选**（stage product）：保存后点 `button id=back` 返回列表，Hollister 关键词筛选仍生效，可直接继续处理下一个产品，无需重新搜索。
- **Configurable 产品的变体无需单独标记**：搜索结果中的 Simple Product（-XS-Green 等）是 "Not Visible Individually" 的变体；在父 Configurable 产品上勾选 Sale 并保存即可（证据仅操作了父产品 ID 126 即判定任务成功）。若需严格逐个标记，则逐行处理，但变体本身不会单独展示 Sale 开关意义。