# Quirks — localhost

- **邮箱不在列表中**：Reviews 网格（index/nnsvenpa6mloayfq 阶段）只显示 Nickname（如 "Hannah Lim"、"customer"），没有邮箱列。必须点击行内 "Edit" 链接进入 `/admin/review/product/edit/id/<ID>/` 详情页才能看到 reviewer 的 Email。
- **"最不满意"需按评分判断，不是按标题**：列表中 "Bad!" 之类的标题只是线索；详情页内才有 rating 星级，需逐条打开比较评分，取最低分那条。
- **Search 是整页刷新**：点击 Search 后页面重载（URL 带 `filter/...name=Circe.../`），所有元素 index 全部变化（证据中 index 从 58xxxxx 变为 59xxxxx/596xxx），必须重新读取 DOM 再操作，不能复用搜索前的 index。
- **过滤输入框是模糊匹配**：输入 "Circe" 会匹配所有名字含 Circe 的产品（如 "Circe Hooded Ice Fleece"），本例返回多条，均需比较。