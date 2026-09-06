# Task: Find what customers dislike about Antonia Racer Tank

Path: Marketing > Reviews (All Reviews) > filter by product name > open each review to read full text.

## Step 1: Navigate to All Reviews
From the admin dashboard (`http://localhost:7780/admin/admin/dashboard/`):
1. `click` the sidebar link with visible text "Marketing".
2. `click` the submenu link with visible text "All Reviews" — lands on the Reviews grid (`/admin/review/product/`).

## Step 2: Reset any stale filter (optional but safe)
If the grid shows a previously applied filter, `click` the button `aria-label=Reset Filter` / visible text "Reset Filter".

## Step 3: Filter reviews by product name
1. `input_text` into the filter field `id=reviewGrid_filter_name name=name` with text `Antonia Racer Tank` (it is the second-to-last filter input, in the "Product" column of the filter row).
2. `click` the button `aria-label=Search` visible text "Search" (top-left toolbar, `title=Search`).
3. The grid re-renders showing only this product's reviews (URL changes to `/admin/review/product/index/filter/...`). Records found count appears above the grid.

## Step 4: Open each review and read the full text
The list view truncates the review text ("Definitely not good for anything high-impact, b..."), so open the detail page for each row:
1. `click` the row (`tr` whose `aria-label` is the edit URL, e.g. `http://localhost:7780/admin/review/product/edit/id/338/`) — or the "Edit" link in its last cell. This opens the "Edit Review" page.
2. Read the full review from the `textarea id=detail name=detail` (full text is present as the textarea's value/text even though it may render empty in the DOM tree text — e.g. "Definitely not good for anything high-impact, but it's very stylish for yoga or something else low impact."). The title `input id=title` ("Summary of Review") gives the summary, e.g. "Not for high impact" / "Zero support/modesty". The rating appears in the Rating section (e.g. 60% = 3 stars).
3. Return to the list via `click` button `id=back` `aria-label=Back` "Back", then open the next row.
4. Repeat for every review row in the filtered result (for Antonia Racer Tank: ids 337, 338, 339 — 339 "A regular or me" by Pearl is positive; 337 and 338 are the negative ones).

## Step 5: Summarize the negative aspects
Key dislikes observed:
- Review 338 (Merrie, "Not for high impact"): not suitable for high-impact activity; only good for low-impact like yoga; stylish though.
- Review 337 (Shaunte, "Zero support/modesty"): zero support, no modesty, would only wear to low-impact classes like yoga.

Finish with `done(text, success=true)` summarizing the disliked aspects.