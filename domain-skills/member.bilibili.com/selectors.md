# Selectors — bilibili.com

| 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |
|---|---|---|---|
| 视频文件上传 | upload 阶段的拖拽上传区域 | `type=file, name=buploader, accept=.mp4` | 页面有多个 `name=buploader`，必须通过 `accept` 属性筛选出视频文件 |
| 封面图片上传 | upload-conver 阶段的封面上传区域 | `type=file, accept=image/png, image/jpeg` | 仅在进入封面编辑阶段时出现 |
| 字幕文件上传 | （备用）上传区域 | `type=file, name=buploader, accept=.txt` | 用于上传字幕，勿与视频上传混淆 |
| 标签输入框 | 标签区域，位于推荐标签上方 | `type=text, placeholder=按回车键Enter创建标签` | 输入后必须触发 Enter 键按下事件才能生效 |