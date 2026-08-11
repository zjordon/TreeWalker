# Selectors — bilibili.com

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
| :--- | :--- | :--- | :--- |
| 视频上传 input | 页面中部上传区域内 | `type=file, accept=.mp4,.flv,...` | 未见 `name` 属性。需与全局残留的 `name=buploader` inputs 区分。 |
| 标题输入框 | 基本设置区域 | `type=text, placeholder=请输入稿件标题` | |
| 创作声明输入框 | 标题下方 | `type=text, placeholder=请选择符合您视频内容的创作声明` | |
| 标签输入框 | 标签区域内 | `type=text, placeholder=按回车键Enter创建标签` | |
| 简介输入框 | 简介区域内 | `contenteditable=true` | |
| 存草稿按钮 | 页面最底部 | `可见文本"存草稿"` | |