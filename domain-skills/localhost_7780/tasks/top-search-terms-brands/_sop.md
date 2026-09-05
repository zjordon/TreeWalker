# 任务：分析 Top Search Terms 中出现最频繁的品牌

本任务纯只读分析，无需任何编辑操作。

## 步骤

1. **进入 Dashboard**：`navigate(url="http://localhost:7780/admin/admin/dashboard/")`（或点击左侧菜单 `<li id=menu-magento-backend-dashboard>` 下的 "Dashboard" 链接）。

2. **滚动到页面底部**：Dashboard 页面较长，`scroll(amount=10, direction="down")`（一次到底），必要时再补 `scroll(amount=1, direction="down")`。目标区块在 Lifetime Sales / Last Orders 之后。

3. **读取 "Top Search Terms" 表格**（`<table id=topSearchGrid_table>`，列：Search Term / Results / Uses）。本例数据行（见 dashboard 阶段）：
   - hollister（Uses 19）
   - Joust Bag（Uses 10）
   - Antonia Racer Tank（Uses 23）
   - Antonia（Uses 结果列空）
   - tanks（Uses 23）

4. **品牌归类与频次统计**：判断哪些搜索词是品牌名——
   - **Antonia**（品牌）：出现于 "Antonia" 和 "Antonia Racer Tank" 两条，合计 2 条，最频繁。
   - **hollister**（品牌）：1 条。
   - 非品牌词："Joust Bag"（产品名）、"tanks"（品类词）。

5. **给出结论并结束**：`done(text="Antonia 是 Top Search Terms 中出现最频繁的品牌（2 条：Antonia、Antonia Racer Tank），其次为 hollister（1 条）", success=true)`。