# Count Total Reviews (Magento Admin @ localhost:7780)

**Goal**: Report the total number of product reviews the shop has received.

## Steps

1. From the admin dashboard (`http://localhost:7780/admin/admin/dashboard/`), click the **Marketing** item in the left main menu (`<a>` with visible text "Marketing", inside `<li id=menu-magento-backend-marketing>`). The menu expands.
2. In the expanded Marketing submenu, click the `<a>` with visible text **"All Reviews"**. This navigates to the Reviews grid (`http://localhost:7780/admin/review/product/index/...`), titled "Reviews".
3. (Optional but observed) Click the **Reset Filter** button (`<button title="Reset Filter" aria-label="Reset Filter">` near the top of the grid, next to "New Review" and "Search" buttons) to ensure no leftover filters hide records. The URL becomes `.../review/product/index/filter//internal_reviews//form_key/...`.
4. Read the total directly from the grid toolbar: the text **"351 records found"** appears between the mass-action dropdown and the "per page" selector (`<select id=reviewGrid_page-limit>`). No pagination or row counting is needed.
5. Answer with `done(text, success)` — e.g. "The shop received 351 reviews in total."

## Notes
- Do NOT count grid rows (only 20 shown per page) — the "N records found" counter above the grid is the authoritative total across all statuses (Approved/Pending/Not Approved).