# Find products with 0 units left (Magento admin)

Task: report the NAMES of all products whose Quantity = 0.

1. If not already on the admin, go to `http://localhost:7780/admin/admin/dashboard/`.
2. Navigate: click the `Catalog` item in the left admin menu (stage dashboard), then click `Products` in the expanded submenu. You land on `http://localhost:7780/admin/catalog/product/` — the Products grid.
3. If an "Active filters: Quantity:" chip is shown above the grid, click the `Remove` button next to it to clear the stale filter, then click `Filters` again to reopen the filter panel.
4. Click the `Filters` button (above the grid, next to `Columns` / `Default View`). The filter panel expands.
5. In the Quantity row, fill BOTH range inputs: `input type=text name=qty[from]` → `0`, and `input type=text name=qty[to]` → `0`. (In the trace these had random ids like `PHS74DC` / `SQ57YUG` — target by `name=qty[from]` / `name=qty[to]`.)
6. Click `Apply Filters` (bottom of the filter panel, next to `Cancel`).
7. The grid re-renders showing only products with Quantity `0.0000`. Results may span multiple pages — check the "records found" count and the page indicator (e.g. "of 11"); use the Next Page button to enumerate every page, collecting each row's product name (the `<div>` in the Name column, e.g. "Mona Pullover Hoodlie").
8. Report the full list of names via `done(text, success)`.