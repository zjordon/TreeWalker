# Quirks — localhost

- 日期输入框是带日期控件的 `<input>`（旁边会出现一个日历 `<button>`）。直接 `input_text` 填写 `M/D/YY` 格式文本即可（录制中即用此方式成功）；不要尝试点击日历弹层选日期。
- 「Show Report」提交是**整页跳转**（URL 变为 `.../bestsellers/filter/<base64>/`），不是 AJAX。点击后需等待页面重载并重新读取 DOM；新页面里 from/to 的 value 会显示为 `5/1/22` / `5/31/23`，可用于确认筛选生效。
- 提交后如果看到 "We couldn't find any records."（bestsellers_1 阶段出现过），说明日期在提交前被清空或未生效，回到筛选表单重新填写即可。
- 空结果页的 from/to 输入框无 value 且有 `placeholder=mm/dd/yyyy`，可据此区分已筛选/未筛选状态。