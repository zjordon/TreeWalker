# 任务：将某产品（所有尺寸变体）的价格降低 $5 — Magento Admin (localhost:7780)

本例：Hollister Backyard Sweatshirt（绿色）共 5 个尺寸变体（XS/S/M/L/XL，SKU MH05-*-Green，ID 111/114/117/120/123），每个都是独立的 Simple Product，必须逐个进入编辑页改价，无批量入口。

## 步骤

1. 进入产品列表：左侧菜单 Catalog → Products（URL: http://localhost:7780/admin/catalog/product/）。
2. 缩小范围到目标变体：
   - 在关键词搜索框 `input id=fulltext placeholder="Search by keyword"` 输入品牌+品名（如 `Hollister Backyard`），点旁边的 Search 按钮（`aria-label=Search`）。
   - 点 Filters 按钮展开筛选面板，在 Name 筛选输入框（`input name=name`）填入 `green`，点 Apply Filters。此时列表只剩 5 条绿色变体（1 页内）。
3. 对每个尺寸变体循环执行（本次顺序 S→XS→M→XL→L，顺序无所谓，5 个都要改）：
   a. 在列表中找到目标行（行内可见产品名如 `Hollister Backyard Sweatshirt-S-Green`），滚动使 `Edit` 链接可见，点击行末的 `a aria-label="Edit Hollister Backyard Sweatshirt-<SIZE>-Green"`。
   b. 进入编辑页后（页面顶部显示该变体名），价格输入框是 `input name="product[price]"`（在 Price 标签旁）。使用 `input_text(index, text, clear=true)` 直接写入新价格 = 原价 − 5（原价可从列表行 Price 列读取，本例 $52.00 → 47）。
   c. 点顶部 `button id=save-button`（可见文本 Save）保存。
   d. 保存成功后点 `button id=back`（可见文本 Back）返回产品列表，继续下一个变体。返回后列表的筛选条件仍在，无需重新搜索。
4. 全部变体改完后，在列表中核对每行 Price 列均为 $47.00，然后 `done`。

## 注意
- 编辑页很长，价格字段在页面靠上位置（Product Name / SKU 之后）；无需滚动到底。
- 每个变体的价格输入框 `id` 每次加载都不同（随机后缀），只能靠 `name="product[price]"` 识别，不要复用旧 index。