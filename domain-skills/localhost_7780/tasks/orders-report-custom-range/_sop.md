# 任务：生成订单报表（自定义日期范围，如 2021-05-01 至 2022-03-31）

Magento Admin (http://localhost:7780/admin/) → Reports → Sales → Orders 报表页，填写 From/To 日期后 Show Report。

## 步骤

1. 从 Dashboard 左侧菜单点击 **Reports**（`<a>` 可见文本 "Reports"，位于 `li#menu-magento-reports-report` 下）。
2. 在展开的子菜单中点击 **Orders**（`<a>` 可见文本 "Orders"），进入 Orders Report 页（/admin/reports/report_sales/sales/）。
3. 页面初始显示 "We couldn't find any records."。在 Filter 表单中填写日期：
   - **From**：`input#sales_report_from`（aria-label/title="From"，placeholder="mm/dd/yyyy"），用 `input_text(index, text="5/1/21", clear=true)` 输入起始日期。
   - **To**：`input#sales_report_to`（aria-label/title="To"），用 `input_text(index, text="3/31/22", clear=true)` 输入结束日期。
   - 日期格式 `mm/dd/yyyy`，2 位年份也可（实测 5/1/21、3/31/22 生效）。
4. 其余筛选保持默认（Date Used=Order Created，Period=Day，Order Status=Any）。
5. 点击 **Show Report**（`button#filter_form_submit`，title="Show Report"）。页面整页跳转到带 `/filter/<base64>/` 的 URL，表格显示记录（如 "33 records found"）。
6. 用 `scroll(amount, direction)` 下翻读取结果表（Interval / Orders / Sales Total 等列）。

## 说明
- “end of March 2022” 实测填 3/31/22（该月最后一天）。