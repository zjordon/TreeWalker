# Quirks — localhost

1. **只需改父商品**（stage 350）：Taurus Elements Shell 是 Configurable Product（ID 350），在其编辑页把 Stock Status 设为 "Out of Stock" 并 Save 即可使整个商品（含所有尺寸/颜色变体）前台显示缺货；无需逐个编辑 16 个 Simple 变体。
2. **表单元素 id 随机**（对比 stage 350 与 350_4）：同一 Stock Status select 的 id 从 `ENCP833` 变为 `QVH54E8`，每次页面加载重新生成。只能通过 `name=product[quantity_and_stock_status][is_in_stock]` 识别，不要记忆/复用 id。
3. **Save 是整页刷新**：点击 `save-button` 后页面全量重载（backend_node_id 全部变化），保存后勿复用旧 index，需重新读 DOM。
4. **父商品行区分**：搜索结果中父商品（Configurable，Quantity 显示 0.0000 且 Salable Quantity 为空）与各变体（Simple，各 100 件）混排；必须点 `aria-label=Edit Taurus Elements Shell`（精确无后缀）的那条。