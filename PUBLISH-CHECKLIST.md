# Publish checklist and steps

Status: built and tested locally only. No repo created, nothing pushed, nothing submitted, no sign-ins. **The owner signs in and presses every submit button; bots do not.**

## A. Decisions (all approved by the owner; no open questions)

| # | Item | Value now in the package | Status |
|---|---|---|---|
| 1 | Author | `howdyollie` (`plugin.json` author.name, LICENSE) | approved |
| 2 | License | MIT, `LICENSE` file, `"license": "MIT"` | approved |
| 3 | Repo | new public GitHub repo `agent-dashboard`: `https://github.com/howdyollie/agent-dashboard` (also `homepage` and `repository` in `plugin.json`) | username confirmed; repo not created yet |
| 4 | Marketplace | cursor.directory (community; this plugin wraps no third-party service). The first-party Cursor Marketplace only if the owner insists | approved |
| 5 | Name / version | `agent-dashboard` / `0.1.0`, display name "Agent Dashboard" (README and `LISTING.md`; the manifest has no display-name field) | approved |
| 6 | Contact | the repo's issues page `https://github.com/howdyollie/agent-dashboard/issues`; no personal email anywhere | approved |


## B. Before publishing (all done locally unless marked)

- [x] Tests pass and the leak grep is clean (`python3 tests/test_all.py`).
- [x] Usage claims match `docs/usage-audit.md`.
- [ ] Real-client test: follow `docs/TEST-INSTALL.md` (Option A works before publishing in the Cursor IDE; a Grok Bot test is only possible after listing).
- [ ] Re-read `LISTING.md` once as a stranger would.

## C. Create the GitHub repo (the owner, signed in to GitHub)

Verified general GitHub steps; nothing here has been run.

1. Go to https://github.com/new. Owner: your account (or the org you chose). Repository name: `agent-dashboard`. Visibility: **Public**. Do **not** tick "Add a README", ".gitignore" or "license" (the folder already has them). Create the repository.
2. In the plugin folder on your computer (the folder that contains `plugin.json`):

```
git init -b main
git add .
git commit -m "agent-dashboard 0.1.0"
git remote add origin https://github.com/howdyollie/agent-dashboard.git
git push -u origin main
```

Alternative with the GitHub CLI (signed in with `gh auth login`): from the same folder, after `git init -b main`, `git add .` and the commit:

```
gh repo create howdyollie/agent-dashboard --public --source=. --push
```

3. Open the repo page and check that `plugin.json`, `mcp.json`, `skills/` and `LICENSE` are at the top level of the repo (not inside a subfolder).

## D. Submit to cursor.directory (the owner signs in and presses Submit)

Source: the cursor.directory project README (cursor/community-plugins), fetched; the live site did not load for the bot, so the form itself has not been seen.

**Verified (from that README and a Cursor staff forum reply):**
1. Go to https://cursor.directory/plugins/new.
2. Sign in with GitHub or Google.
3. Paste the GitHub repo URL: `https://github.com/howdyollie/agent-dashboard`.
4. Click **Submit**.
- The directory auto-detects components from standard paths and runs an automated security scan of the repo on submission; a human moderation queue exists. cursor.directory is the community marketplace and was described by Cursor staff as the recommended place for most plugins. Plugins published there appear only on cursor.directory, not in the first-party Cursor Marketplace.

**Not verified. Confirm on the form / at submission:**
- Whether there are extra fields beyond the repo URL. If there are, paste from `LISTING.md`: name `agent-dashboard`; short description (the line under "Short description"); long description (the "Long description" block); tags (the "Tags" line); author, license and repository (the lines above "Requirements").
- **MCP server detection.** The README lists `.mcp.json` as the detected path for MCP servers; this plugin ships `mcp.json` (the Agent Plugins / Cursor path). If the preview shows 3 skills but no MCP server, tell the bot: the fix is to add an identical `.mcp.json` copy, then commit and push, and re-run the tests.
- Whether the directory shows a copy-paste install command or an "Add to Cursor" button (update the install line in `LISTING.md` once you see it).
- How long the scan and review take, and where status is shown (the first-party marketplace has no status page; the directory is unknown).
- The first-party Cursor Marketplace, if you ever want it: https://cursor.com/marketplace/publish (submit the repo link; manual review; staff follow up by email; the forum thread names a contact address for questions). Not recommended as the default.

## E. After submission

- Run `docs/TEST-INSTALL.md` Option B and report back.
- If the scan flags anything: the only network code is `dashlib/sources.py` (`urllib`, `http_json` sources you configure, GET only, no redirects, no retries); the audit and README describe it.

## F. Risks to accept or fix

- `python3` must be on PATH (Windows needs `python`); if Python is missing no tool can explain it, only the docs can.
- `mcp.json` has been written to work with both `${PLUGIN_ROOT}` (the standard) and `${CURSOR_PLUGIN_ROOT}` (Cursor, which documents that it does not expand `${PLUGIN_ROOT}`). Tested by simulating both; not yet tested in a live client.
- Connector-fed panels need a bot turn per refresh; file and endpoint panels refresh with plain cron. README and listing say so.
- Keys may sit in a local `secrets.env`; the plugin warns on loose permissions but cannot protect a computer others can read.
- Usage numbers come from one VM; token counts are estimates.
