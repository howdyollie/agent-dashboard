# Test-install (about 10 minutes)

Goal: check that the plugin starts in a real client and that the four everyday prompts work, before it is published.

## Before you start

- You need Python 3.9 or newer (`python3 --version`; on Windows the command is `python`, see the README).
- Which client can test it before publishing?
  - **Cursor IDE, from a local folder: possible now.** Verified in the Cursor docs ("Test plugins locally").
  - **Grok Bot: only after publishing.** As far as we know, Grok Bot loads plugins from the Cursor dashboard or a marketplace, not from the local plugins folder. *Not verified from the docs; confirm in your client.* So a Grok Bot test happens after the plugin is listed.
  - Enterprise Cursor accounts: an admin may have switched off "Allow Local Plugin Imports", which also disables "Import from Disk".

## Option A: Cursor IDE, before publishing

1. Copy this whole plugin folder to `~/.cursor/plugins/local/agent-dashboard` (on Windows: `%USERPROFILE%\.cursor\plugins\local\agent-dashboard`). `plugin.json` must sit directly inside that folder. Do not copy a symlink that points outside the plugins folder; Cursor skips those.
2. Restart Cursor, or run **Developer: Reload Window**.
3. Open **Customize** in the sidebar. You should see the plugin `agent-dashboard` with 3 skills (`setup-my-dashboard`, `manage-dashboard`, `add-dashboard-source`) and 1 MCP server (`dashboard`). If the server shows an error, open its log or tell your agent "the dashboard tool did not start" and see "If something fails" in the README.
4. If a server needs approval, approve it. Cursor shows plugin servers with a `plugin-` prefix.

## Option B: after publishing

1. Open the plugin's page on cursor.directory (listing name `agent-dashboard`).
2. Install it from there. *The exact button text on the directory page is not verified; confirm it.* In Cursor, installed plugins and the Install button for marketplace plugins are under **Customize** (choose project or user scope).
3. Start a new chat so the bot picks up the plugin.

## The four prompts

Say these one at a time, in a new chat:

| You say | Success looks like |
|---|---|
| "set up my dashboard" | The bot asks what you want to see, then one setup call; your browser opens a page with your panels, each tagged SAMPLE. A folder `.agent-dashboard` now exists in your home folder. |
| "add my X stats" | An X stats panel appears (sample numbers). The page reopens or refreshes. |
| "open my dashboard" | The page opens again. No chat about data or keys. |
| "move sales to the top" | The sales panel is first when the page reopens. You can also drag a card (press and hold the grip). |

Also try once: press and hold a card's grip and drag it to reorder; reload the page and check the order stayed.

## What to report back

- Which client and version, and which option (A or B).
- For each of the four prompts: worked / did not work, and the exact bot message if it failed.
- Did the page open by itself? Did the MCP server show as running in Customize?
- Anything confusing in the first minute (wording, number of questions the bot asked).
- Whether the bot ever asked you to paste a key or password into chat (it must not).
- If you can see usage: roughly how much usage the whole test used. The plugin's own measured numbers are in `docs/usage-audit.md`; the bot's own chat tokens are not part of them.

## Clean up

To remove the test: uninstall the plugin in Customize (or delete the local plugin folder), and delete the `.agent-dashboard` folder in your home folder if you want the sample data gone.
