# Selectors — douyin.com

| 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |
|---|---|---|---|
| 视频上传 input | 初始上传页面的核心文件输入框 | `type=file`, `accept=video/x-flv,video/mp4,video/x-m4v,video/*,...` | 唯一的视频接收 input。必须使用 `upload_file`。 |
| 自定义封面上传 input | 封面编辑模态框内的图片上传框 | `type=file`, `accept=image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif` | 模态框中可能存在多个同名 input，其中带有 `class=semi-upload-hidden-input` 用于初始上传，`semi-upload-hidden-input-replace` 用于替换。 |
| 自主声明确定按钮 | 自主声明弹窗底部的确认按钮 | `type=button`, 可见文本`确定` | 初始加载时 `disabled=true`，必须先选择一个声明选项才会激活。 |