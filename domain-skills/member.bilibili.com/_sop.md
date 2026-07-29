# 哔哩哔哩视频投稿流程

## upload-video-content: 上传与发布

### 步骤 1: 进入投稿页面
- 从创作中心首页 (`member.bilibili.com/platform/home`) 点击左侧导航栏的 `a id=nav_upload_btn` (可见文本"投稿")。
- 在展开的投稿类型选项中，点击 `span` (可见文本"视频投稿")。
- 页面跳转至 `member.bilibili.com/platform/upload/video/frame`，进入 **upload** 阶段。

### 步骤 2: 上传视频文件
- 在 upload 阶段，页面存在多个隐藏/可见的文件上传组件。
- 定位主要视频上传框：带有 `compound_components=(name=Browse Files...)` 且 `accept` 属性包含 `.mp4` 的 `input type=file`。
- 使用直接文件注入方法将视频文件路径设置到该 input 中。
- 上传开始后，页面通过 SPA 局部刷新进入 **publish** 阶段，URL 保持不变。此时左侧出现视频上传进度（如"上传中... 89%"），右侧出现视频信息编辑表单。
- **注意**：必须等待进度条消失并显示"上传完成"后，才能进行最终提交。

### 步骤 3: 编辑视频信息 (publish 阶段)
在右侧的"基本设置"表单中依次填写：
- **封面**：点击 `span` (可见文本"封面设置") 或 "智能封面"。如果要自定义上传封面，点击后页面会进入 **upload-conver** 阶段（见步骤 4）。
- **标题**：在 `input type=text placeholder=请输入稿件标题` 中输入标题（最大长度 80 字符）。
- **创作声明**：在 `input type=text placeholder=请选择符合您视频内容的创作声明` 中点击选择（下拉选项）。
- **分区**：点击显示当前分区（如"游戏"或"科技数码"）的 `div` 区域以展开选择列表。
- **标签**：在 `input type=text placeholder=按回车键Enter创建标签` 中输入标签文字，然后按下 `Enter` (回车键) 将其添加为标签。
- **简介**：在 `div contenteditable=true` 富文本框中输入视频简介（最大 2000 字）。

### 步骤 4: 上传自定义封面 (upload-conver 阶段)
- 如果在步骤 3 点击了封面设置，页面（SPA）会进入 **upload-conver** 阶段。
- 在"封面制作"弹窗/区域中，找到"上传封面"按钮。
- 定位封面上传 input：带有 `accept=image/png, image/jpeg` 的 `input type=file`。
- 使用直接文件注入将图片路径设置到该 input 中。
- 上传完成后，在弹窗右下角点击 `div` (可见文本"完成") 以确认并返回 publish 阶段。

### 步骤 5: 发布稿件
- 确认视频上传已完成（左侧显示"上传完成"）。
- 在页面最底部右侧，点击 `span` (可见文本"存草稿") 暂存。