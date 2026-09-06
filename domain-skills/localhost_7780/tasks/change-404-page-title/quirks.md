# Quirks — localhost

- page 阶段（CMS 页面列表）：Action 列的 Edit/Delete/View 链接并非每行默认展开。证据中只有第一行（404 Not Found）已展开 Edit 链接；其余行只显示 Select 按钮，需先 `click` Select 才出现 Edit。404 Not Found 是列表第一行，通常已可直接点 Edit。
- page_2 → 1 阶段：刚进入编辑页时 DOM 可能尚未渲染出 Page Title 输入框和 Save 按钮（page_2 快照中只有 Back/Delete Page），需 `wait(1-2)` 秒等表单字段加载后再操作。
- 标题输入框 id（如 YA7B21G）是动态生成的，每次加载不同，勿依赖具体 id 值；用 `name=title` + type=text 定位。