---
name: manage-dashboard
description: Use when a dashboard exists and the user says "open/show/refresh my dashboard", or asks to add, remove, rename or reorder panels ("add my X stats", "remove ads", "move sales to the top").
---

# Manage the dashboard

One local HTML page built from `config.json` by a plain script (no LLM at refresh). Change it only with the `dashboard` tools.

- **Open / show / refresh:** call `open_dashboard` once (rebuilds, then opens the page). Give the path if `opened` is false. `no_open: true` to only refresh. No dashboard yet: use `setup-my-dashboard`.
- **Add** ("add my X stats", "add sales"): `add_panel {type}`, then `open_dashboard`. If that panel already exists, they probably want real data: use `add-dashboard-source`.
- **Edit** title/options/source: `update_panel`. **Remove:** `remove_panel` (confirm first if it has a real source). **Reorder:** `reorder_panels` with EVERY id once. Types: x_stats, website_visits, sales, ads, bot_activity (`list_panel_library {type}` for one).
- Check every panel in the result is `ok`; report any error in plain words. Errors end with "Fix: ..." - pass it on, and do not repeat a failed call more than once without changing something.
- Drag order in the page is saved in that browser only; to make it permanent, ask the user to paste the order from the Layout menu, then `reorder_panels`.

Rules: never put a key, token or password in any tool argument (a real source uses a file or an env var NAME). Don't hand-edit `config.json`. Keep sample mode until the user confirms a real source works; say which panels are real. To refresh on a schedule suggest cron running `python3 scripts/build.py`, never a bot turn.
