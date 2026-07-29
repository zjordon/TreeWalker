# Quirks — bilibili.com

1. **多文件输入框陷阱 (Element Identity Ambiguity)**：投稿页面同时存在 3-4 个 `input type=file name=buploader`（或无 name），分别用于视频、字幕、附件等。**绝对不能仅靠 `name` 或顺序定位**，必须交叉检查 `accept` 属性：视频选 `.mp4`，封面选 `image/png`。

2. **封面 Input 的条件性渲染 (Hidden Dependency / Sequencing)**：在 **publish** 阶段，`accept=image/png` 的封面上传 Input 并不存在于 DOM 中。必须先点击 `span` (可见文本"封面设置") 触发 SPA 切换到 **upload-conver** 阶段，该 Input 才会被动态渲染到 DOM 中，随后才能进行文件注入。

3. **标签添加需要键盘事件 (Action-method requirements)**：在 `input placeholder=按回车键Enter创建标签` 中输入文本后，普通的点击或失去焦点不会创建标签。**必须模拟 `Enter` 键的 keydown 事件**才能将文本转化为标签 chip。

4. **全程 SPA 无 URL 变化 (SPA Stage Transition)**：从进入 `.../upload/video/frame` 到视频上传完毕、填写信息、进入封面裁剪、返回信息页，整个流程 URL 完全不变。必须依赖 DOM 内容（如是否出现"上传完成"、是否存在特定弹窗）来判断当前处于哪个阶段，不能用 URL 区分。

5. **Hidden File Input 注入方式 (Action-method requirements)**：所有上传 Input（视频和封面）虽然可能被标记为 `visible=True`，但它们是通过 wrapper div 模拟样式的，直接 click 会触发无法控制的操作系统级弹窗。**必须使用直接文件路径注入 (upload_file by index/path) 的方式**处理这些 input。