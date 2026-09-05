# Task: Create a Bestsellers report for a custom date range (e.g. 05/01/2022 – 05/31/2023)

Magento admin on localhost:7780.

## Step 1: Navigate to Bestsellers report
From any admin page, open the **Reports** menu in the left sidebar (`<a>` with visible text "Reports"), then **Products → Bestsellers** (`<a>` visible text "Bestsellers" under the Products section of the expanded Reports menu). Page title becomes "Bestsellers Report".

(If starting from the Orders sales report at `/admin/reports/report_sales/sales/...`, the Bestsellers link is directly visible in the Reports menu dropdown.)

## Step 2: Set the From date
Locate `<input title=From type=text id=sales_report_from name=from placeholder=mm/dd/yyyy required=true>` in the Filter form (`<form id=filter_form>`).
Use `input_text(index=…, text="5/1/22", clear=true)` — Magento accepts `M/D/YY` short format.

## Step 3: Set the To date
Locate `<input title=To type=text id=sales_report_to name=to placeholder=mm/dd/yyyy required=true>`.
Use `input_text(index=…, text="5/31/23", clear=true)`.

Note: Period (`<select id=sales_report_period_type name=period_type>` with options Day|Month|Year) defaults to Day; leave as-is unless the task asks for another granularity.

## Step 4: Submit
Click `<button title="Show Report" id=filter_form_submit>` (visible text "Show Report"). This reloads the page with the filtered result (URL changes to `/admin/reports/report_sales/bestsellers/filter/<base64>/`).

## Step 5: Read results
After the page reloads, the table header shows "Interval / Product / Price / Order Quantity" and "N records found". Scroll (`scroll(amount, direction)` as needed) to read product rows. Task complete → `done(text, success)`.