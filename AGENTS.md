# slack-showcase-bot

Scope: Showcase Bot, a tour of what a Slack app can do in the AI Uplift for EA
Slack (`T094GSSSF3M`): slash commands, modals, App Home, persistent state,
streaming Claude answers with tools over Alejo's GitHub, Canvases, Slack Lists,
unfurls, shortcuts, the assistant panel and a Workflow Builder step.

- Visibility: public repo; state stays in ignored `data/showcase.db`
- Google account: none
- Still running or billing: `showcase.service` on the agent server (once
  installed); Anthropic API usage when Alejo asks it things

## Rules

- Features that touch GitHub or Claude answer only `SHOWCASE_OWNER` (Alejo):
  slash commands are visible workspace-wide, and these act on Alejo's accounts.
  Keep that gate on every new feature of that kind.
- Link unfurls skip private repos, since anyone in the channel sees the card.
- GitHub calls go through the `gh` CLI signed in as `alejoacelas`.
- [manifest.yaml](manifest.yaml) is the app's configuration; paste changes into
  the app's **App Manifest** page in Slack.

## Code

- [showcase/app.py](showcase/app.py): every Slack handler; `MENU` lists the demos.
- [showcase/brain.py](showcase/brain.py): Claude tool loop with GitHub tools.
- [showcase/gh.py](showcase/gh.py) and [showcase/store.py](showcase/store.py):
  GitHub calls and SQLite state.

Run locally: `~/best/areas/dotfiles/bin/op-agent run --env-file=deploy/alejo/mac.env.tpl -- uv run python -m showcase.app`.
Stop the server copy first; Slack splits events between connections.

## Deploy (agent server)

Runs as `alejo`'s user service ([deploy/alejo/showcase.service](deploy/alejo/showcase.service))
from `~/work/slack-showcase-bot`. Deploy: push, then
`ssh agent-server 'cd ~/work/slack-showcase-bot && git pull && ~/.local/bin/uv sync --frozen && systemctl --user restart showcase'`.

## Credentials (personal 1Password; Mac copies in `mac-agents`, server copies in `server-agents`)

| Variable | Item | Field |
|---|---|---|
| `SLACK_BOT_TOKEN` | Showcase Bot Slack | `bot_token` |
| `SLACK_APP_TOKEN` | Showcase Bot Slack | `app_token` |
| `ANTHROPIC_API_KEY` | ANTHROPIC_API_KEY — personal-apps-2026-10 | `credential` |
