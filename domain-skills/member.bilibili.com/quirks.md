# Quirks — bilibili.com

1. **多文件输入陷阱 (Element Identity Ambiguity)**
   在 `upload-video` 阶段，DOM 底部始终潜伏着多个 `name=buploader` 的全局隐藏 `<input type=file>` （如 `accept=.txt` 用于字幕，`accept=.zip` 用于附件，以及冗余的 `accept=.mp4` 视频输入）。**上传主视频时，务必通过 `accept` 属性中没有 `name=buploader` 或位于主上传区域内的那个 `<input type=file>` 来区分**，否则可能将视频错误上传到字幕通道。

2. **标签提交方式 (Action Method Requirement)**
   添加标签时，在 `placeholder=按回车键Enter创建标签` 的输入框中输入文本后，必须使用 `send_keys(keys="Enter")` 动作来提交标签使其生效，不能依赖失焦或点击空白处。

3. **封面上传模态框触发 (Hidden Dependency)**
   封面上传 `<input type=file accept=image/png, image/jpeg>` 在默认阶段不在主 DOM 树中（被掩藏）。必须先点击可见文本为"添加封面"的按钮触发模态框后，该 file input 才会渲染并可被索引到。

4. **无文件对话框交互 (Action Method Requirement)**
   页面上所有的上传区域（视频、封面）点击后都会触发 OS 原生文件选择对话框。必须严格使用 `upload_file(index, path)` 进行直接文件注入，不要尝试 `click` 上传按钮。