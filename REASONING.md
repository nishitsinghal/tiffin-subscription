# Reasoning

## Understanding the problem
The core requirement was: "bill a customer only for the days they were actually served."
That means pause/resume can't just be a day *counter* — it needs real start/end dates,
because a bill is calculated per calendar month, and pauses can span across month
boundaries or happen more than once in a month.

## Data model decisions
- `Customer` holds identity + monthly `plan_price`. It does NOT store a `status` or
  `paused_days` column directly — those are *derived*, not stored, to avoid the data
  getting out of sync with reality.
- A separate `Pause` table stores `start_date` and `end_date` per pause event.
  `end_date = NULL` means the customer is still paused right now. This lets a customer
  be paused and resumed multiple times across their history, and each event is auditable.
- Bill for a given month = `plan_price / days_in_month * (days_in_month - paused_days_in_month)`,
  where `paused_days_in_month` is computed by intersecting each `Pause` range with the
  target month and counting unique days (so overlapping/duplicate pauses aren't double-counted).

## Bugs found & fixed during testing
1. **CSS didn't load** — the stylesheet link pointed at `https://jsdelivr.net` (the CDN's
   homepage) instead of the actual Bootstrap file URL, so every page rendered unstyled.
   Fixed by linking the correct Bootstrap 5 CDN path.
2. **Pause tracking was just a counter** — the first version had a `paused_days` integer
   that only ever went up via an "add pause day" button, with no actual dates and no
   monthly reset. This didn't match the brief (pause/resume with real days, billed
   per month). Replaced it with the date-based `Pause` model above.
3. **Sorting by "status"** required extra handling since status isn't a stored DB column —
   it's computed from whether a customer has an open pause. Sorting by status is done in
   Python after fetching matching rows, since it can't be expressed as a simple SQL `ORDER BY`.

## Testing approach
- Manually seeded a customer with a 5-day pause earlier in the current month and verified
  the bill came out to `plan_price * 25/30` for a 30-day month — confirmed correct.
- Tested pause → resume → pause again on the same customer to confirm multiple `Pause`
  records don't double-count overlapping days.
- Tested duplicate phone number rejection on customer creation.
- Tested search with partial name and partial phone matches, and pagination across pages.

## What I'd still improve with more time
- Currently bills only the *current* month; a "generate bill for any past month" view
  would need a month/year selector on the dashboard.
- No edit/delete UI for an existing pause record if a date was entered wrong.