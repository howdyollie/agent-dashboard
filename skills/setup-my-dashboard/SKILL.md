---
name: setup-my-dashboard
description: Use for first-time setup: "set up my dashboard", "create/make me a dashboard", or when dashboard tools say no dashboard exists. Asks what to show (X, website visits, sales, ads, bots), then creates it with sample data, builds and opens it.
---

# Set up my dashboard

Goal: one sentence from the user to an open page, in one tool call. The user edits no files.

1. Ask once what they want to see: **X stats**, **website visits**, **sales**, **ads**, **bot activity** (any, or "all"). Skip the question if they already said. Optional title.
2. Call the `setup` tool with {title?, panels:[x_stats|website_visits|sales|ads|bot_activity]}. It creates the folder (default hidden `.agent-dashboard` in the home folder, or `DASHBOARD_HOME`), adds SAMPLE panels, builds and opens the page. Safe to repeat; it only adds missing panels. Do not also call `init`, `add_panel` or `rebuild`.
3. Tell them in plain words: the page is open (if `opened` is false or a `hint` came back, give the `page` path to open by hand); panels show fictional SAMPLE numbers until connected; next they can say "add my X stats", "move sales to the top", "open my dashboard". Panels can be dragged (press and hold the grip).
4. Ask which panel to make real first, then use `add-dashboard-source`.

Errors end with "Fix: ..." in plain words: pass that on. Do not repeat a failed call more than once without changing something. If Python is missing the plugin cannot start: point to the README "If something fails" section.

Rules: never ask for a key, token or password in chat; never call sample numbers real; use the tools, not hand-edited files.
