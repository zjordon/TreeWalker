# 抖音创作者中心 - 视频上传与发布流程

URL入口: `https://creator.douyin.com/creator-micro/content/upload?enter_from=publish`

## 上传与初始化阶段

**步骤 1: 处理草稿弹窗（条件触发）**
- 如果页面出现文本 `你还有上次未发布的视频，是否继续编辑？`，根据任务需求点击 `继续编辑` (span) 或 `放弃` (span)。
- 如果没有该弹窗，继续下一步。

**步骤 2: 上传视频文件**
- 定位视频文件输入框：`<input type=file accept=video/x-flv,video/mp4,...>`。
- 必须使用动作 `upload_file(index, path)` 直接注入文件路径。
- 上传成功后，页面将自动跳转到编辑页面：`https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page`。
- 注意：上传过程中会显示进度条（如 `上传过程中请不要删除/移动文件` 及百分比），需使用 `wait(seconds)` 等待解析完成，直到出现 `基础信息` 表单。

## 编辑与设置阶段

**步骤 3: 填写基本信息（标题与描述）**
- 等待编辑表单加载（页面左侧出现 `基础信息`、`作品描述`）。
- 定位标题输入框：`<input type=text placeholder=填写作品标题，为作品获得更多流量>`。使用 `input_text`。
- 定位作品描述框：`<div contenteditable=true>`（位于标题下方，字数统计旁）。使用 `input_text`。

**步骤 4: 设置封面（可选/任务驱动）**
- 封面分为横封面（4:3）和竖封面（3:4）。如果需要自定义封面：
- 点击包含文本 `选择封面` 的元素（通常为 div/span），打开封面编辑模态框。
- 在模态框中，定位 `<input type=file accept=image/png,image/jpeg,...>` 上传封面图，使用 `upload_file`。
- 上传完成后，点击 `完成` (button) 应用横封面。如需竖封面，点击 `设置竖封面` (button) 重复操作。

**步骤 5: 选择合集与自主声明**
- **合集**：滚动页面，找到 `请选择合集` 触发器（Semi UI 自定义下拉），用 `select_dropdown(index=<触发器 index>, value=<合集名>)` 直接选中（例：`value="TreeWalker合集"`）。**不要先 click 触发器、也不要 click 选项**——`select_dropdown` 会自己展开+按文本选中（值会被记进回放，可变量化）。
- **自主声明**：找到 `请选择自主声明` 文本并点击。页面将弹出模态框 `对作品内容添加声明`。
  - 模态框加载时，底部的 `确定` 按钮初始状态为 `disabled=true`。
  - 必须先点击列表中的一个选项（如 `<label>` 包含的 `内容由AI生成` 或 `无需添加自主声明`）。
  - 选中后，`确定` 按钮变为可用状态，点击 `确定` 完成声明。

**步骤 6: 发布设置与提交**
- 滚动页面至底部 `发布设置` 区域。
- 根据需求设置 `谁可以看`（公开/好友可见/仅自己可见，点击对应的 `<label>` 标签）。
- 定位底部的发布按钮：`<button>发布</button>` 或 `<button>暂存离开</button>`。
- 点击 `发布` 提交视频。