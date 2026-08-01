# Selectors — douyin.com

| 元素用途 (element purpose) | 怎么找到它 (how to find it) | 稳定标识 (stable identity) | 备注 (notes) |
| --- | --- | --- | --- |
| 视频上传 input | 上传页面主卡片中央，"点击上传" 文字附近 | `type=file, accept=video/x-flv,video/mp4,...` | 隐藏的 input 元素，必须通过直接注入文件路径上传。 |
| 草稿恢复选项 | 页面顶部横幅提示文字区 | 可见文本"继续编辑", 可见文本"放弃" | 仅在存在未完成草稿时动态出现。 |
| 标题输入框 | "基础信息" -> "作品描述" 上方 | `type=text, placeholder=填写作品标题，为作品获得更多流量` | 限制30字。 |
| 描述输入框 | 标题输入框正下方 | `contenteditable=true` | 普通文本输入，支持富文本。 |
| 封面上传 input (主表单) | "设置封面" 下方的 "上传封面" 按钮附近 | `type=file, accept=image/png,image/jpeg,image/jpg, name=upload-btn` | 隐藏 input。 |
| 封面上传 input (编辑弹窗) | 封面编辑弹窗(modal)内的 "上传封面" 区域 | `type=file, accept=image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif` | 隐藏 input。注意区分弹窗内外的同名输入框，此处多了 bmp/webp/tif 支持。 |