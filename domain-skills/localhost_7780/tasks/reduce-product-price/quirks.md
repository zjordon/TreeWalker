# Quirks — localhost

1. **可配置产品的每个尺寸是独立 Simple Product，必须逐个编辑**：列表中不出现父级 "Hollister Backyard Sweatshirt" 条目（父级 Type=Configurable，被 Name=green/keyword 筛选后不匹配或不出现在结果中）。修改父级价格不会改变子变体价格，必须对每个 `MH05-<SIZE>-Green` 变体单独进编辑页改价并保存（本任务共 5 个：XS/S/M/L/XL）。
2. **关键词搜索 alone 不够**：只搜 `Hollister Backyard` 会返回 2040 条（其他颜色变体也命中）。需再用 Filters → Name=`green` 才能收敛到 5 条绿色变体、单页显示。
3. **保存后返回用 Back 按钮而非重新导航**：`click(id=back)` 返回产品列表时筛选条件保留，直接复用列表继续下一个变体；重新 navigate 会丢失筛选。
4. **价格输入框 id 每次加载随机**（S9AYEYL / HDP4NAC / EJOPRWJ / IHQWRH9 / AHUTXUY），跨步骤不可复用 index，进入新编辑页后须重新按 `name=product[price]` 在当前 DOM 中定位。