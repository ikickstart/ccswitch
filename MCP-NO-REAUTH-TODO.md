# TODO: MCP servers that survive a `ccswitch` account switch (no re-auth)

Why this exists: the claude.ai connectors (Slack, Atlassian, Drive, ...) are authorized **per Claude account on Anthropic's side**, so a different account has never authorized them and ccswitch cannot copy that. **Local MCP servers** (added with `claude mcp add`) sign in to Slack / Atlassian / Salesforce directly, never to Claude, so their login does not change when the Claude login does.

Endpoints and flags below came from web search (2026-10-22) and are marked **verify** where the sources disagreed or were third-party. Check the vendor docs before relying on them. Never put a token in `~/.claude.json` or a committed `.mcp.json`.

## 0. Prerequisites (once)

- [x] ccswitch fix landed (`ecdcb08`, 2026-10-22): a switch moves only `claudeAiOauth` and never reads, copies or overwrites `mcpOAuth`; a save stores only the login block. 47 unit tests pass against the fake Keychain (8 new, shown failing on the old code).
- [ ] Test whether a **user-scope** server's OAuth token is shared across projects or stored per project (GitHub issue "Share MCP OAuth tokens globally for user-scoped servers"). Authenticate Slack in project A, open project B, run `/mcp`. If it asks again, prefer the token-based or tool-held-login routes below (Jira API token, Salesforce `sf` login, `mcp-remote`).
- [ ] In claude.ai Settings -> Connectors, turn off the claude.ai copies of Slack / Atlassian / Salesforce on every account, so the same tool does not appear twice.
- [ ] Remove stale entries from `~/.claude/mcp-needs-auth-cache.json` (today it lists "claude.ai Google Cloud BigQuery").

## 1. Slack

- [ ] Add: `claude mcp add slack --transport http --scope user https://mcp.slack.com/mcp`
- [ ] Ask the Slack workspace admin whether the Slack MCP app needs approval (Enterprise Grid or paid workspaces may need it). **verify**
- [ ] In a session run `/mcp`, pick `slack`, complete the browser sign-in once.
- [ ] Prove it: run one read-only Slack tool, then `ccswitch switch <other account>`, open a new session (or `/mcp`), run the same tool. No sign-in prompt = done.
- [ ] Fallback if OAuth keeps re-prompting: a Slack user token held in the Keychain and passed by env (needs a Slack app with the scopes you use); never in the repo.

## 2. Jira (Atlassian Rovo MCP)

- [ ] Confirm the endpoint in Atlassian's docs: community posts say `https://mcp.atlassian.com/v1/mcp`, one doc lists `/v1/mcp/authv2`. **verify**
- [ ] Preferred (no browser, never expires on a switch): API-token auth. Atlassian says the server supports API tokens for headless use. In Atlassian admin, check API-token auth is allowed for the Rovo MCP server on your site, create a token, and pass it as the header Atlassian documents (**verify exact header format**), the token coming from an env var, not typed into a file.
- [ ] Otherwise OAuth 2.1: `claude mcp add atlassian --transport http --scope user <endpoint>`, then `/mcp` once.
- [ ] Prove it the same way as Slack (read a Jira issue, switch account, read again).
- [ ] Note the rate limits per plan (Atlassian: 500/hour Free, 1000/hour Standard) when you share one token across accounts.

## 3. Salesforce (SF)

- [ ] Choose the route:
  - (a) **Local `salesforcecli` MCP server over the `sf` CLI login** (my pick: the login lives in the `sf` CLI, so Claude's account is irrelevant). Authorize each org once: `sf org login web --alias <alias>`. Then `claude mcp add salesforce --scope user -- npx -y @salesforce/mcp --orgs <alias> ...` **verify the exact package name, toolset flags and `--orgs` syntax in Salesforce's docs.**
  - (b) Hosted Salesforce MCP (GA): admin activates hosted servers, creates an External Client App (OAuth 2.0 + PKCE), and Claude Code connects through `mcp-remote`. **verify the production and sandbox URL patterns (the beta URLs changed at GA).**
- [ ] Pin `--orgs` to specific aliases. Do **not** use `ALLOW_ALL_ORGS`.
- [ ] Keep a separate alias for sandbox and production.
- [ ] Prove it: run a read-only query, `ccswitch switch <other account>`, run it again.

## 4. BigQuery (optional, you listed BQ)

- [ ] Managed server: enable the BigQuery API and MCP in the project, endpoint `https://bigquery.googleapis.com/mcp`, Google credentials (never Claude's). Read-only SQL tool only (`execute_sql_readonly`).
- [ ] Or the local MCP Toolbox for Databases with `gcloud auth application-default login`.

## 5. Final check (after all of the above)

- [ ] `ccswitch switch` through all four accounts in turn; in a fresh session each time, `/mcp` shows Slack / Jira / Salesforce connected with no auth prompt.
- [ ] The key NAMES of the Keychain item still include `mcpOAuth` after the switches (check names only; never print values): `python3 - <<'PY'\nimport subprocess,json,getpass\nd=json.loads(subprocess.run(['security','find-generic-password','-s','Claude Code-credentials','-a',getpass.getuser(),'-w'],capture_output=True,text=True).stdout)\nprint(sorted(d.keys()))\nPY`
- [ ] After ONE real `! claude auth login --email <addr> && ccswitch save` (re-login), run the same name check: `mcpOAuth` should still be listed. If it is gone, that is Claude Code behaviour, not ccswitch; add a re-inject step to ccswitch (back up the key names/values to a private file first).
- [ ] If anything re-prompts, record which server and whether it was after a switch or a new project, then pick the next route in that section.
