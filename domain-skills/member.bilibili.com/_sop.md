# Bilibili 视频投稿与存草稿流程

## navigate-to-video-upload

**Step 1: 进入创作中心首页**
- 确保 URL 为 `https://member.bilibili.com/platform/home`。

**Step 2: 进入视频投稿页面**
- 在左侧导航栏底部找到 `data-tw-jsclick=1` 且可见文本为"视频投稿"的 `<a>` 元素，执行 `click`。
- 页面将 SPA 切换至 URL `https://member.bilibili.com/platform/upload/video/frame`，进入视频上传阶段。

## upload-video-with-cover

**Step 3: 上传视频文件**
- 在页面左侧找到可见文本包含"点击上传或将视频拖拽到此区域"和"上传视频"的上传区域，寻找其对应隐藏的 `<input type=file accept=.mp4,.flv,...>` （accept 属性包含大量视频扩展名，且非 `name=buploader`）。
- 执行 `upload_file(index, path)` 直接注入视频文件路径。

**Step 4: 等待视频上传完成**
- 执行 `wait(seconds)` 等待视频上传处理。上传完成后，DOM 中会出现 `title=视频文件名` 的 `<div>`，且伴随文本"上传完成"。

**Step 5: 添加并上传封面**
- 找到可见文本为"添加封面"的 `<div>` 并执行 `click`，将打开封面制作弹窗。
- 在弹窗中找到可见文本包含"上传封面"和"拖拽图片或点击上传"的区域，寻找其对应的 `<input type=file accept=image/png, image/jpeg>`。
- 执行 `upload_file(index, path)` 直接注入封面图片路径。
- 完成后找到可见文本为"完成"的 `<div>` 并执行 `click` 以关闭弹窗。

## fill-video-metadata-and-save-draft

**Step 6: 填写标题**
- 找到 `placeholder=请输入稿件标题` 的 `<input type=text>` 元素，执行 `input_text(index, text)`。

**Step 7: 选择创作声明（可选）**
- 找到 `placeholder=请选择符合您视频内容的创作声明` 的 `<input type=text>` 元素，对其执行 `select_dropdown(index, value)`，`value` 为目标选项的可见文本（如 `value="个人观点，仅供参考"`）。
- **不要先 `click` 触发器、也不要 `click` 选项**——这是自定义下拉，`select_dropdown` 会自己展开 + 按文本选中（值会被记进回放、可变量化）。

**Step 8: 选择分区（可选）**
- 找到当前分区显示文本（如"游戏"或"人工智能"）所在的容器 `<div>`（即分区触发器），对其执行 `select_dropdown(index, value)`，`value` 为目标分区名（如 `value="科技数码"`，可用选项的 `title` 属性里的名字）。
- **不要先 `click` 触发器、也不要 `click` 选项**——`select_dropdown` 会自己展开 + 按文本选中。

**Step 9: 添加标签**
- 找到 `placeholder=按回车键Enter创建标签` 的 `<input type=text>` 并执行 `click` 或 `input_text`。
- 输入标签文本后，必须执行 `send_keys(keys="Enter")` 来提交标签。可重复此操作添加多个标签。

**Step 10: 填写简介**
- 找到 `contenteditable=true` 的 `<div>`（通常在"简介"标题下方），执行 `input_text(index, text)`。

**Step 11: 保存草稿**
- 执行 `scroll(amount, direction="down")` 向下滚动页面，直到底部按钮可见。
- 找到可见文本为"存草稿"的 `<span>` 元素，执行 `click`。
- 页面将导航至 `https://member.bilibili.com/platform/upload-manager/article?group=draft`，表示草稿保存成功。