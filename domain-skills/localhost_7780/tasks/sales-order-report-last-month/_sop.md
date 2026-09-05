# Generate a sales order report for last month (Magento admin)

Context: "today" is given in the task (e.g. 3/15/2023). Compute last month's full range: From = 1st day of previous month, To = last day of previous month (e.g. 2/1/2023 → 2/28/2023). Date format accepted by the form is `m/d/yy` (observed values `1/1/23`, `2/28/23`).

1. Start at the admin dashboard `http://localhost:7780/admin/admin/dashboard/`. In the left admin menu, click the `<a>` with visible text `Reports` (inside `li id=menu-magento-reports-report`).
2. In the Reports submenu, click the `<a>` with visible text `Orders` — lands on Orders Report page `http://localhost:7780/admin/reports/report_sales/sales/`. Default view shows "We couldn't find any records." until filtered.
3. Locate the From date input: `<input title=From type=text id=sales_report_from name=from>` inside `form id=filter_form`. Use `input_text(index, "2/1/23")` (substitute your computed first-of-month).
4. Locate the To date input: `<input title=To type=text id=sales_report_to name=to>`. Use `input_text(index, "2/28/23")` (substitute computed end-of-month). If the value doesn't stick, re-click the input and re-enter.
5. Optionally click somewhere neutral on `form id=filter_form` to blur the date inputs and close any calendar popup (a small calendar-toggle `<button>` appears next to each date input).
6. Click the submit button: `<button title="Show Report" id=filter_form_submit>` with visible text `Show Report`. This triggers a full page load to a URL like `/admin/reports/report_sales/sales/filter/<base64>`.
7. After the report loads, verify "N records found" appears (e.g. 18) and a Total row summarizing Orders / Sales Items / Sales Total for the range. Read totals from the Total row directly under the header row (Interval, Orders, Sales Items, Sales Total, ...).
8. `scroll(amount, "down")` as needed to read rows, then `done(text, success)` with the report summary.