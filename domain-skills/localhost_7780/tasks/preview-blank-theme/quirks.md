# Quirks — localhost

- Themes 列表页加载后，"View" 链接出现在每行末尾；若列表含多行主题，需按行内可见文本 "Magento Blank" 定位对应行的 View，避免点错行。
- 点击 "View" 后 URL 为 `/admin/admin/system_design_theme/`（列表页 URL 相同格式），实际进入的是主题编辑页——以页面出现 "Theme: Magento Blank" 文本判断是否成功，不要依赖 URL 区分列表页与编辑页。