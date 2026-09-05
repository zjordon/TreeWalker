# Quirks — localhost

- **属性集必须先切到 "Top" 才有 Size/Color**：simple_3 阶段（Default 属性集）表单没有 `product[size]`/`product[color]` 下拉；simple_4 阶段切到 Top 后它们才出现。先改 Attribute Set 再填值，否则切换会重排表单（记录中切换后 input 的 index 已变化）。
- **保存是整页跳转而非 AJAX**：点击 `Save` 后 URL 从 `/catalog/product/new/...` 变为 `/catalog/product/edit/id/2046/...`，以 URL 变化判断保存成功，不要停留在原页等待 toast。
- Size/Color 在编辑页重新渲染后 id 会变（如 size 由 DAJ2JC4 变为 TJFDHDJ），只能靠 `name=product[size]` / `name=product[color]` 定位，index 跨阶段不可复用。