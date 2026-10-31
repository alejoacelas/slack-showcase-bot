"""Showcase Bot: a tour of what a Slack app can do, wired to Alejo's GitHub and Claude.

Usage: uv run python -m showcase.app
Env: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, ANTHROPIC_API_KEY, SHOWCASE_OWNER (Slack user ID).
GitHub and Claude features answer only the owner, since they act on the owner's accounts.
"""

import io
import logging
import os
import re
import shlex
import threading
import time
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from slack_bolt import App, Assistant
from slack_bolt.adapter.socket_mode import SocketModeHandler

from showcase import brain, gh, store

log = logging.getLogger("showcase")
OWNER = os.environ["SHOWCASE_OWNER"]
app = App(token=os.environ["SLACK_BOT_TOKEN"])
AUTH = app.client.auth_test()
BOT_USER, TEAM, WORKSPACE_URL = AUTH["user_id"], AUTH["team_id"], AUTH["url"]
OWNER_ONLY = "This demo acts on Alejo's GitHub and Claude accounts, so it only answers Alejo."


# ---------- helpers ----------

def dm(user: str) -> str:
    return app.client.conversations_open(users=user)["channel"]["id"]


def md(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def button(text: str, action_id: str, value: str = "x", style: str | None = None) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": text, "emoji": True}, "action_id": action_id, "value": value}
    if style:
        b["style"] = style
    return b


def bar(n: int, total: int, width: int = 12) -> str:
    filled = round(width * n / total) if total else 0
    return "🟩" * filled + "⬜" * (width - filled)


def stream_to_thread(channel: str, thread_ts: str, user: str, messages: list[dict], tools: bool = True) -> None:
    """Stream Claude's answer into a thread with Slack's native streaming, showing tool use as status."""
    streamer = app.client.chat_stream(channel=channel, thread_ts=thread_ts, recipient_team_id=TEAM, recipient_user_id=user)

    def status(s: str) -> None:
        try:
            app.client.assistant_threads_setStatus(channel_id=channel, thread_ts=thread_ts, status=s)
        except Exception:
            pass

    try:
        brain.answer(messages, on_text=lambda t: streamer.append(markdown_text=t), on_status=status, tools=tools)
    finally:
        streamer.stop()


# ---------- the menu ----------

MENU = [
    ("🗳️ Live poll", "menu_poll", "Votes persist and the bars update in place."),
    ("✅ Add a to-do", "menu_todo", "Saved to your Home tab; tick it off there."),
    ("🐙 Repo card", "menu_repo", "Live stats for any of your repos, with actions."),
    ("✨ Ask Claude", "menu_ask", "Streams the answer as it's written, using your repos as tools."),
    ("📄 README → Canvas", "menu_canvas", "Turns a repo README into a native Slack Canvas."),
    ("📈 Activity chart", "menu_chart", "Draws your last two weeks of commits and uploads the image."),
    ("🧾 To-dos → Slack List", "menu_list", "Exports your to-dos into a real Slack List with checkboxes."),
    ("⏰ Schedule a message", "menu_schedule", "Slack delivers it later, even if the bot is offline."),
    ("🏴‍☠️ Custom persona", "menu_persona", "The same bot, posting under another name and face."),
    ("🧭 Hidden tricks", "menu_tour", "Unfurls, reactions, shortcuts, the assistant panel and a Workflow Builder step."),
]


def menu_blocks() -> list[dict]:
    blocks = [md("*Showcase Bot* · pick a demo. Everything you try gets stamped on your *Home* tab.")]
    for text, action, desc in MENU:
        blocks.append({**md(f"*{text}*\n{desc}"), "accessory": button("Try it", action)})
    return blocks


@app.command("/showcase")
def cmd_showcase(ack, respond, command):
    ack()
    store.mark(command["user_id"], "slash")
    respond(blocks=menu_blocks(), text="Showcase menu")


# ---------- Home tab: persistent state, rendered per person ----------

def home_view(user: str) -> dict:
    tried = store.tried(user)
    stamps = " ".join(("✅ " if k in tried else "◻️ ") + v for k, v in store.FEATURES.items())
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🎪 Showcase Bot"}},
        md(f"*Feature passport: {len(tried)}/{len(store.FEATURES)} stamped*\n{stamps}"),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{store.counter():,} interactions handled so far · state lives in SQLite on the agent server"}]},
        {"type": "divider"},
        md("*Your to-dos*"),
    ]
    todos = store.todos(user)
    if todos:
        options = [{"text": {"type": "mrkdwn", "text": f"~{t}~" if d else t}, "value": str(i)} for i, t, d in todos]
        el = {"type": "checkboxes", "action_id": "todo_toggle", "options": options}
        done = [o for o, (_, _, d) in zip(options, todos) if d]
        if done:
            el["initial_options"] = done
        blocks.append({"type": "actions", "elements": [el]})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Nothing yet. Try `/todo buy milk`, the ⚡ *Quick capture* shortcut, or react 📝 to a message in our DM."}]})
    blocks.append({"type": "actions", "elements": [button("➕ Add", "menu_todo"), button("🧹 Clear done", "todo_clear"),
                                                   button("🧾 Export to Slack List", "menu_list")]})
    polls = store.my_polls(user)
    if polls:
        blocks += [{"type": "divider"}, md("*Your latest polls*")]
        for pid in polls:
            q, opts, voters = store.poll(pid)
            total = sum(map(len, voters))
            lines = "\n".join(f"{bar(len(v), total, 8)} {o} · {len(v)}" for o, v in zip(opts, voters))
            blocks.append(md(f"*{q}*\n{lines}"))
    if user == OWNER:
        blocks += [{"type": "divider"}, md("*GitHub pulse*")]
        try:
            lines = [f"• <{r['html_url']}|{r['name']}> · pushed {gh.ago(r['pushed_at'])}" + (" 🔒" if r["private"] else "")
                     for r in gh.repos()[:6]]
            blocks.append(md("\n".join(lines)))
        except Exception as e:
            blocks.append(md(f"_Couldn't reach GitHub: {e}_"))
    blocks += [{"type": "divider"}, md("*Demos*")]
    row = []
    for text, action, _ in MENU:
        row.append(button(text, action))
        if len(row) == 5:
            blocks.append({"type": "actions", "elements": row})
            row = []
    if row:
        blocks.append({"type": "actions", "elements": row})
    return {"type": "home", "blocks": blocks}


def refresh_home(user: str) -> None:
    try:
        app.client.views_publish(user_id=user, view=home_view(user))
    except Exception:
        log.exception("home refresh failed")


@app.event("app_home_opened")
def on_home(event):
    if event.get("tab") == "home":
        refresh_home(event["user"])


# ---------- to-dos ----------

TODO_MODAL = {
    "type": "modal", "callback_id": "todo_modal", "title": {"type": "plain_text", "text": "Quick capture"},
    "submit": {"type": "plain_text", "text": "Save"},
    "blocks": [{"type": "input", "block_id": "t", "label": {"type": "plain_text", "text": "To-do"},
                "element": {"type": "plain_text_input", "action_id": "v", "placeholder": {"type": "plain_text", "text": "Ship the feedback bot"}}}],
}


@app.command("/todo")
def cmd_todo(ack, respond, command):
    ack()
    user, text = command["user_id"], command.get("text", "").strip()
    store.mark(user, "todo")
    if text:
        store.add_todo(user, text)
        respond(f"Added *{text}* to your Home tab.")
    else:
        todos = store.todos(user)
        respond("\n".join(("✅ ~" + t + "~") if d else ("◻️ " + t) for _, t, d in todos) or "No to-dos yet. Try `/todo water the plants`.")
    refresh_home(user)


@app.action("menu_todo")
@app.shortcut("quick_capture")
def open_todo(ack, body, client):
    ack()
    client.views_open(trigger_id=body["trigger_id"], view=TODO_MODAL)


@app.view("todo_modal")
def save_todo(ack, body, view):
    ack()
    user = body["user"]["id"]
    store.add_todo(user, view["state"]["values"]["t"]["v"]["value"])
    store.mark(user, "todo")
    refresh_home(user)


@app.action("todo_toggle")
def toggle_todo(ack, body, action):
    ack()
    user = body["user"]["id"]
    store.set_done(user, {int(o["value"]) for o in action.get("selected_options", [])})
    refresh_home(user)


@app.action("todo_clear")
def clear_todo(ack, body):
    ack()
    store.clear_done(body["user"]["id"])
    refresh_home(body["user"]["id"])


@app.event("reaction_added")
def on_reaction(event):
    """React 📝 to a message in the bot's DM and it becomes a to-do."""
    item = event.get("item", {})
    if event.get("reaction") != "memo" or item.get("type") != "message" or not item.get("channel", "").startswith("D"):
        return
    msgs = app.client.conversations_history(channel=item["channel"], latest=item["ts"], inclusive=True, limit=1)["messages"]
    if msgs:
        user = event["user"]
        store.add_todo(user, msgs[0].get("text", "")[:200] or "(empty message)")
        store.mark(user, "reaction")
        app.client.reactions_add(channel=item["channel"], timestamp=item["ts"], name="white_check_mark")
        refresh_home(user)


# ---------- polls ----------

def poll_blocks(pid: int) -> list[dict]:
    q, opts, voters = store.poll(pid)
    total = sum(map(len, voters))
    blocks = [md(f"🗳️ *{q}*")]
    for i, (o, v) in enumerate(zip(opts, voters)):
        who = " ".join(f"<@{u}>" for u in v[:6]) + (f" +{len(v) - 6}" if len(v) > 6 else "")
        blocks.append({**md(f"*{o}*\n{bar(len(v), total)} {len(v)}  {who}"), "accessory": button("Vote", f"vote_{i}", f"{pid}:{i}")})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{total} vote{'s' * (total != 1)} · one per person, change it anytime"}]})
    return blocks


@app.command("/poll")
def cmd_poll(ack, respond, command):
    ack()
    try:
        parts = shlex.split(command.get("text", ""))
    except ValueError:
        parts = []
    if len(parts) < 3:
        respond('Usage: `/poll "Lunch?" "Pizza" "Tacos" "Salad"`')
        return
    pid = store.new_poll(command["user_id"], parts[0], parts[1:10])
    store.mark(command["user_id"], "poll")
    respond(response_type="in_channel", blocks=poll_blocks(pid), text=parts[0])


@app.action(re.compile(r"vote_\d+"))
def on_vote(ack, body, action, respond):
    ack()
    pid, opt = map(int, action["value"].split(":"))
    store.vote(pid, body["user"]["id"], opt)
    store.bump()
    respond(replace_original=True, response_type="in_channel", blocks=poll_blocks(pid), text="Poll")


POLL_MODAL = {
    "type": "modal", "callback_id": "poll_modal", "title": {"type": "plain_text", "text": "New poll"},
    "submit": {"type": "plain_text", "text": "Post"},
    "blocks": [
        {"type": "input", "block_id": "q", "label": {"type": "plain_text", "text": "Question"},
         "element": {"type": "plain_text_input", "action_id": "v", "initial_value": "Where should the next meetup be?"}},
        {"type": "input", "block_id": "o", "label": {"type": "plain_text", "text": "Options, one per line"},
         "element": {"type": "plain_text_input", "action_id": "v", "multiline": True, "initial_value": "Oxford\nLondon\nOnline"}},
    ],
}


@app.action("menu_poll")
def open_poll(ack, body, client):
    ack()
    client.views_open(trigger_id=body["trigger_id"], view=POLL_MODAL)


@app.view("poll_modal")
def save_poll(ack, body, view, client):
    ack()
    user = body["user"]["id"]
    v = view["state"]["values"]
    opts = [o.strip() for o in v["o"]["v"]["value"].splitlines() if o.strip()][:9]
    pid = store.new_poll(user, v["q"]["v"]["value"], opts)
    store.mark(user, "poll")
    client.chat_postMessage(channel=dm(user), blocks=poll_blocks(pid), text="Poll")
    refresh_home(user)


# ---------- GitHub: repo cards, canvases, charts, issues ----------

def repo_card(name: str) -> list[dict]:
    r = gh.repo(name)
    issues, prs = gh.open_counts(name)
    commits = gh.commits(name, 3)
    lines = "\n".join(f"• <{c['html_url']}|`{c['sha'][:7]}`> {c['commit']['message'].splitlines()[0][:80]} · {gh.ago(c['commit']['author']['date'])}"
                      for c in commits) or "_No commits yet_"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": f"🐙 {r['name']}"}},
        md(f"{r['description'] or '_No description_'}\n<{r['html_url']}|Open on GitHub>"),
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Visibility*\n{'🔒 private' if r['private'] else '🌍 public'}"},
            {"type": "mrkdwn", "text": f"*Language*\n{r.get('language') or '—'}"},
            {"type": "mrkdwn", "text": f"*Last push*\n{gh.ago(r['pushed_at'])}"},
            {"type": "mrkdwn", "text": f"*Open*\n{issues} issues · {prs} PRs"},
        ]},
        md(f"*Latest commits*\n{lines}"),
        {"type": "actions", "elements": [
            button("✨ Explain it", "repo_explain", name, "primary"),
            button("📄 README → Canvas", "repo_canvas", name),
            button("🐞 New issue", "repo_issue", name),
        ]},
    ]


def repo_options() -> list[dict]:
    return [{"text": {"type": "plain_text", "text": (("🔒 " if r["private"] else "") + r["name"])[:75]}, "value": r["name"]}
            for r in gh.repos()[:100]]


def repo_picker(purpose: str, title: str) -> dict:
    return {
        "type": "modal", "callback_id": "repo_pick", "private_metadata": purpose,
        "title": {"type": "plain_text", "text": title}, "submit": {"type": "plain_text", "text": "Go"},
        "blocks": [{"type": "input", "block_id": "r", "label": {"type": "plain_text", "text": "Repository"},
                    "element": {"type": "static_select", "action_id": "v", "options": repo_options()}}],
    }


@app.command("/repo")
def cmd_repo(ack, respond, command, client):
    ack()
    user = command["user_id"]
    if user != OWNER:
        respond(OWNER_ONLY)
        return
    name = command.get("text", "").strip()
    store.mark(user, "repo")
    if not name:
        client.views_open(trigger_id=command["trigger_id"], view=repo_picker("card", "Repo card"))
        return
    try:
        respond(blocks=repo_card(name), text=name)
    except Exception as e:
        respond(f"Couldn't load `{name}`: {e}")


@app.action("menu_repo")
@app.action("menu_canvas")
def open_repo_picker(ack, body, client, action):
    ack()
    if body["user"]["id"] != OWNER:
        client.chat_postMessage(channel=dm(body["user"]["id"]), text=OWNER_ONLY)
        return
    purpose = "canvas" if action["action_id"] == "menu_canvas" else "card"
    client.views_open(trigger_id=body["trigger_id"], view=repo_picker(purpose, "README → Canvas" if purpose == "canvas" else "Repo card"))


@app.view("repo_pick")
def picked_repo(ack, body, view, client):
    ack()
    user = body["user"]["id"]
    name = view["state"]["values"]["r"]["v"]["selected_option"]["value"]
    if view["private_metadata"] == "canvas":
        threading.Thread(target=make_canvas, args=(user, name)).start()
    else:
        store.mark(user, "repo")
        client.chat_postMessage(channel=dm(user), blocks=repo_card(name), text=name)


def make_canvas(user: str, name: str) -> None:
    channel = dm(user)
    readme = gh.readme(name) or f"# {name}\n\nThis repo has no README yet."
    try:
        cid = app.client.canvases_create(title=f"{name} · README", document_content={"type": "markdown", "markdown": readme[:90000]})["canvas_id"]
        app.client.canvases_access_set(canvas_id=cid, access_level="write", user_ids=[user])
        store.mark(user, "canvas")
        app.client.chat_postMessage(channel=channel, text=f"📄 Made a Canvas from *{name}*'s README, and you can edit it: {WORKSPACE_URL}docs/{TEAM}/{cid}")
    except Exception as e:
        app.client.chat_postMessage(channel=channel, text=f"Canvas creation failed: {e}")
    refresh_home(user)


@app.action("repo_canvas")
def btn_canvas(ack, body, action):
    ack()
    if body["user"]["id"] == OWNER:
        threading.Thread(target=make_canvas, args=(body["user"]["id"], action["value"])).start()


@app.action("repo_explain")
def btn_explain(ack, body, action):
    ack()
    user = body["user"]["id"]
    if user != OWNER:
        return
    channel = dm(user)
    q = f"Explain what the {action['value']} repo does, how it's structured and what changed recently. Keep it to a short overview."
    root = app.client.chat_postMessage(channel=channel, text=f"✨ *Explaining `{action['value']}`…*")
    store.mark(user, "ask")
    threading.Thread(target=stream_to_thread, args=(channel, root["ts"], user, [{"role": "user", "content": q}])).start()


def issue_modal(repo: str | None, title: str, body: str) -> dict:
    sel = {"type": "static_select", "action_id": "v", "options": repo_options()}
    if repo:
        sel["initial_option"] = next((o for o in sel["options"] if o["value"] == repo), sel["options"][0])
    return {
        "type": "modal", "callback_id": "issue_modal", "title": {"type": "plain_text", "text": "New GitHub issue"},
        "submit": {"type": "plain_text", "text": "Create"},
        "blocks": [
            {"type": "input", "block_id": "r", "label": {"type": "plain_text", "text": "Repository"}, "element": sel},
            {"type": "input", "block_id": "t", "label": {"type": "plain_text", "text": "Title"},
             "element": {"type": "plain_text_input", "action_id": "v", "initial_value": title[:150]}},
            {"type": "input", "block_id": "b", "optional": True, "label": {"type": "plain_text", "text": "Body"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True, "initial_value": body[:2900]}},
        ],
    }


@app.action("repo_issue")
def btn_issue(ack, body, action, client):
    ack()
    if body["user"]["id"] == OWNER:
        client.views_open(trigger_id=body["trigger_id"], view=issue_modal(action["value"], "", ""))


@app.shortcut("message_to_issue")
def shortcut_issue(ack, shortcut, client):
    ack()
    user = shortcut["user"]["id"]
    if user != OWNER:
        client.chat_postEphemeral(channel=shortcut["channel"]["id"], user=user, text=OWNER_ONLY)
        return
    text = shortcut["message"].get("text", "")
    try:
        link = client.chat_getPermalink(channel=shortcut["channel"]["id"], message_ts=shortcut["message"]["ts"])["permalink"]
    except Exception:
        link = ""
    body = f"{text}\n\n—\nFrom Slack{': ' + link if link else ''}"
    client.views_open(trigger_id=shortcut["trigger_id"], view=issue_modal(None, text.splitlines()[0] if text else "", body))


@app.view("issue_modal")
def create_issue(ack, body, view, client):
    ack()
    user = body["user"]["id"]
    v = view["state"]["values"]
    repo = v["r"]["v"]["selected_option"]["value"]
    try:
        url = gh.create_issue(repo, v["t"]["v"]["value"], v["b"]["v"].get("value") or "")
        store.mark(user, "issue")
        client.chat_postMessage(channel=dm(user), text=f"🐞 Opened {url}")
    except Exception as e:
        client.chat_postMessage(channel=dm(user), text=f"Issue creation failed: {e}")


@app.action("menu_chart")
def btn_chart(ack, body):
    ack()
    user = body["user"]["id"]
    if user != OWNER:
        app.client.chat_postMessage(channel=dm(user), text=OWNER_ONLY)
        return
    threading.Thread(target=post_chart, args=(user,)).start()


PALETTE = [(46, 134, 222), (235, 87, 87), (39, 174, 96), (242, 153, 74), (155, 81, 224), (86, 204, 242)]


def post_chart(user: str) -> None:
    data = gh.activity(14)
    days = [(date.today() - timedelta(days=d)).isoformat() for d in range(13, -1, -1)]
    W, H, pad = 1200, 620, 70
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
    except OSError:
        font = big = ImageFont.load_default()
    totals = [sum(c.get(day, 0) for c in data.values()) for day in days]
    peak = max(totals + [1])
    d.text((pad, 20), f"Commits per day, last 14 days ({sum(totals)} total)", fill=(30, 30, 40), font=big)
    bw = (W - 2 * pad) / len(days)
    for i, day in enumerate(days):
        y = H - pad
        for k, (name, counts) in enumerate(data.items()):
            n = counts.get(day, 0)
            h = (H - 2 * pad - 60) * n / peak
            if n:
                d.rectangle([pad + i * bw + 6, y - h, pad + (i + 1) * bw - 6, y], fill=PALETTE[k % len(PALETTE)])
            y -= h
        d.text((pad + i * bw + 8, H - pad + 8), day[5:], fill=(110, 110, 120), font=font)
    for k, name in enumerate(data):
        d.rectangle([pad + k * 190, 62, pad + k * 190 + 16, 78], fill=PALETTE[k % len(PALETTE)])
        d.text((pad + k * 190 + 22, 58), name[:16], fill=(60, 60, 70), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    app.client.files_upload_v2(channel=dm(user), file=buf.getvalue(), filename="activity.png", title="Your commits, last 14 days",
                               initial_comment="📈 Drawn just now from the GitHub API with Pillow, then uploaded as a file.")
    store.mark(user, "chart")
    refresh_home(user)


# ---------- Claude: /ask, the assistant panel, plain DMs, thread summaries ----------

@app.command("/ask")
def cmd_ask(ack, respond, command):
    ack()
    user, q = command["user_id"], command.get("text", "").strip()
    if user != OWNER:
        respond(OWNER_ONLY)
        return
    if not q:
        respond("Usage: `/ask what did I ship this week?`")
        return
    channel = dm(user)
    root = app.client.chat_postMessage(channel=channel, text=f"✨ *You asked:* {q}")
    respond(f"Answering in our DM, streaming as it's written → <{WORKSPACE_URL}archives/{channel}/p{root['ts'].replace('.', '')}|open>")
    store.mark(user, "ask")
    threading.Thread(target=stream_to_thread, args=(channel, root["ts"], user, [{"role": "user", "content": q}])).start()


ASK_MODAL = {
    "type": "modal", "callback_id": "ask_modal", "title": {"type": "plain_text", "text": "Ask Claude"},
    "submit": {"type": "plain_text", "text": "Ask"},
    "blocks": [{"type": "input", "block_id": "q", "label": {"type": "plain_text", "text": "Question"},
                "element": {"type": "plain_text_input", "action_id": "v", "multiline": True,
                            "initial_value": "Which of my repos changed most this week, and what's the story of those changes?"}}],
}


@app.action("menu_ask")
def open_ask(ack, body, client):
    ack()
    if body["user"]["id"] != OWNER:
        client.chat_postMessage(channel=dm(body["user"]["id"]), text=OWNER_ONLY)
        return
    client.views_open(trigger_id=body["trigger_id"], view=ASK_MODAL)


@app.view("ask_modal")
def ask_submitted(ack, body, view):
    ack()
    user = body["user"]["id"]
    q = view["state"]["values"]["q"]["v"]["value"]
    channel = dm(user)
    root = app.client.chat_postMessage(channel=channel, text=f"✨ *You asked:* {q}")
    store.mark(user, "ask")
    threading.Thread(target=stream_to_thread, args=(channel, root["ts"], user, [{"role": "user", "content": q}])).start()


assistant = Assistant()


@assistant.thread_started
def assistant_started(say, set_suggested_prompts, context):
    say("Hi! I can dig through your GitHub repos, explain code, and file issues. What would you like to know?")
    set_suggested_prompts(prompts=[
        {"title": "What changed this week?", "message": "What did I push across my repos this week? Group it by project."},
        {"title": "Explain a repo", "message": "Explain what slack-moderation-bot does and how it's deployed."},
        {"title": "Find loose ends", "message": "Which of my repos have no README or look abandoned?"},
        {"title": "Draft an issue", "message": "Draft a GitHub issue for adding tests to slack-moderation-bot, and show it to me before filing."},
    ])


@assistant.user_message
def assistant_message(payload, client, context, set_status, set_title):
    user, channel, thread = context.user_id, context.channel_id, context.thread_ts
    if user != OWNER:
        client.chat_postMessage(channel=channel, thread_ts=thread, text=OWNER_ONLY)
        return
    store.mark(user, "assistant")
    set_status("is thinking…")
    history = client.conversations_replies(channel=channel, ts=thread, limit=40)["messages"]
    messages = [{"role": "assistant" if m.get("bot_id") else "user", "content": m.get("text") or " "} for m in history]
    messages = brain_messages(messages)
    if len(messages) <= 1:
        try:
            set_title(payload.get("text", "")[:60])
        except Exception:
            pass
    stream_to_thread(channel, thread, user, messages)


app.use(assistant)


def brain_messages(msgs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in msgs:
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + m["content"]
        else:
            out.append(dict(m))
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


@app.event("message")
def on_dm(event, client):
    """Plain DMs (Messages tab): reply in a thread with the same agent."""
    if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
        return
    user = event.get("user")
    if user != OWNER:
        client.chat_postMessage(channel=event["channel"], text="Hi! Try `/showcase` for the menu, or open my *Home* tab.")
        return
    thread = event.get("thread_ts") or event["ts"]
    if event.get("thread_ts"):
        history = client.conversations_replies(channel=event["channel"], ts=thread, limit=40)["messages"]
        msgs = [{"role": "assistant" if m.get("bot_id") else "user", "content": m.get("text") or " "} for m in history]
    else:
        msgs = [{"role": "user", "content": event.get("text", "")}]
    store.mark(user, "ask")
    stream_to_thread(event["channel"], thread, user, brain_messages(msgs))


@app.shortcut("summarize_thread")
def shortcut_summary(ack, shortcut, client, respond):
    ack()
    user = shortcut["user"]["id"]
    if user != OWNER:
        respond(OWNER_ONLY)
        return
    msg = shortcut["message"]
    try:
        replies = client.conversations_replies(channel=shortcut["channel"]["id"], ts=msg.get("thread_ts", msg["ts"]), limit=100)["messages"]
        text = "\n".join(f"<@{m.get('user', 'bot')}>: {m.get('text', '')}" for m in replies)
        scope = f"{len(replies)} messages"
    except Exception:
        text, scope = msg.get("text", ""), "this message (I'm not in this channel, so I can't read the thread)"
    store.mark(user, "summary")
    respond(f"🧵 *Summary of {scope}* (only you can see this)\n{brain.summarize(text)}")


@app.event("app_mention")
def on_mention(event, say):
    say(text="👋 I'm the Showcase Bot. DM me or try `/showcase`.", thread_ts=event["ts"])


# ---------- link unfurls ----------

GH_LINK = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)(?:/(issues|pull)/(\d+))?")


@app.event("link_shared")
def on_link(event, client):
    unfurls = {}
    for link in event.get("links", []):
        m = GH_LINK.match(link["url"])
        if not m:
            continue
        owner, name, kind, num = m.groups()
        try:
            r = gh.api(f"/repos/{owner}/{name}")
            if r["private"]:
                continue  # never show private repo details to whoever can see the channel
            if num:
                it = gh.api(f"/repos/{owner}/{name}/issues/{num}")
                state = "🟣 merged" if it.get("pull_request", {}).get("merged_at") else ("🟢 open" if it["state"] == "open" else "🔴 closed")
                text = f"*<{it['html_url']}|#{num} {it['title']}>*\n{state} · by {it['user']['login']} · {it['comments']} comments\n{(it.get('body') or '')[:200]}"
            else:
                text = f"*<{r['html_url']}|{r['full_name']}>*\n{r['description'] or ''}\n⭐ {r['stargazers_count']} · {r.get('language') or '—'} · pushed {gh.ago(r['pushed_at'])}"
            unfurls[link["url"]] = {"blocks": [md(text)]}
        except Exception:
            continue
    if unfurls:
        client.chat_unfurl(channel=event["channel"], ts=event["message_ts"], unfurls=unfurls)
        store.mark(event["user"], "unfurl")


# ---------- scheduled messages, personas, Slack Lists, the tour ----------

DURATION = re.compile(r"^(\d+)\s*([smhd])\s+(.+)$", re.S)


@app.command("/later")
def cmd_later(ack, respond, command):
    ack()
    m = DURATION.match(command.get("text", "").strip())
    if not m:
        respond("Usage: `/later 10m stretch your legs` (s, m, h or d)")
        return
    n, unit, text = int(m.group(1)), m.group(2), m.group(3)
    post_at = int(time.time() + n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])
    post_at = max(post_at, int(time.time()) + 15)
    app.client.chat_scheduleMessage(channel=dm(command["user_id"]), post_at=post_at, text=f"⏰ {text}")
    store.mark(command["user_id"], "schedule")
    respond(f"Scheduled for <!date^{post_at}^{{time}} ({{date_short_pretty}})|later>. Slack holds it, so it arrives even if the bot is down.")


SCHEDULE_MODAL = {
    "type": "modal", "callback_id": "schedule_modal", "title": {"type": "plain_text", "text": "Schedule a message"},
    "submit": {"type": "plain_text", "text": "Schedule"},
    "blocks": [
        {"type": "input", "block_id": "w", "label": {"type": "plain_text", "text": "When"},
         "element": {"type": "datetimepicker", "action_id": "v", "initial_date_time": int(time.time()) + 120}},
        {"type": "input", "block_id": "t", "label": {"type": "plain_text", "text": "Message"},
         "element": {"type": "plain_text_input", "action_id": "v", "initial_value": "Hello from the past 👋"}},
    ],
}


@app.action("menu_schedule")
def open_schedule(ack, body, client):
    ack()
    view = {**SCHEDULE_MODAL}
    view["blocks"] = [dict(b) for b in SCHEDULE_MODAL["blocks"]]
    view["blocks"][0] = {**view["blocks"][0], "element": {**view["blocks"][0]["element"], "initial_date_time": int(time.time()) + 120}}
    client.views_open(trigger_id=body["trigger_id"], view=view)


@app.view("schedule_modal")
def schedule_submitted(ack, body, view, client):
    v = view["state"]["values"]
    when = int(v["w"]["v"]["selected_date_time"])
    if when < time.time() + 15:
        ack(response_action="errors", errors={"w": "Pick a time at least a minute from now."})
        return
    ack()
    user = body["user"]["id"]
    client.chat_scheduleMessage(channel=dm(user), post_at=when, text=f"⏰ {v['t']['v']['value']}")
    client.chat_postMessage(channel=dm(user), text=f"Scheduled for <!date^{when}^{{time}} on {{date_short_pretty}}|later>.")
    store.mark(user, "schedule")
    refresh_home(user)


PERSONAS = [
    ("Captain Showcase", ":pirate_flag:", "Arr! Same bot, different hat. Any message can carry its own name and icon."),
    ("Professor Pixel", ":teacher:", "As you can see, the `chat:write.customize` scope lets an app pick a persona per message."),
    ("Robo-Butler", ":robot_face:", "Your bidding, sir or madam? Handy for one bot that speaks as several services."),
]


@app.action("menu_persona")
def btn_persona(ack, body, client):
    ack()
    user = body["user"]["id"]
    channel = dm(user)
    for name, icon, text in PERSONAS:
        client.chat_postMessage(channel=channel, text=text, username=name, icon_emoji=icon)
    store.mark(user, "persona")
    refresh_home(user)


@app.action("menu_list")
def btn_list(ack, body, client):
    ack()
    user = body["user"]["id"]
    todos = store.todos(user)
    if not todos:
        client.chat_postMessage(channel=dm(user), text="Add a to-do first (`/todo …`), then export.")
        return
    try:
        created = client.slackLists_create(name=f"Showcase to-dos · {datetime.now():%d %b %H:%M}", todo_mode=True)
        list_id = created["list_id"]
        schema = created.get("list_metadata", {}).get("schema", [])
        title_col = next((c["id"] for c in schema if c.get("is_primary_column")), schema[0]["id"] if schema else None)
        done_col = next((c["id"] for c in schema if c.get("type") == "todo_completed"), None)
        for _, text, done in reversed(todos):
            fields = [{"column_id": title_col, "rich_text": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}]}]}]
            if done_col and done:
                fields.append({"column_id": done_col, "checkbox": True})
            client.slackLists_items_create(list_id=list_id, initial_fields=fields)
        client.slackLists_access_set(list_id=list_id, access_level="write", user_ids=[user])
        store.mark(user, "list")
        client.chat_postMessage(channel=dm(user), text=f"🧾 Exported {len(todos)} to-dos to a Slack List: {WORKSPACE_URL}lists/{TEAM}/{list_id}")
    except Exception as e:
        log.exception("list export failed")
        client.chat_postMessage(channel=dm(user), text=f"Slack List export failed: {e}")
    refresh_home(user)


TOUR = """*🧭 Hidden tricks: things that work without a button*
• *Link unfurls:* paste a public `github.com` repo, issue or PR link here and I attach a live card.
• *Reaction trigger:* react 📝 to any message in this DM and it becomes a to-do on your Home tab.
• *Message shortcuts:* on any message, open ⋮ → *More message shortcuts* → *Create GitHub issue* or *Summarize with Claude*.
• *Global shortcut:* type `/` in any composer, or use the ⚡ menu → *Quick capture*.
• *Assistant panel:* click the ✨ AI icon at the top right of Slack and pick Showcase Bot; it stays open beside any channel.
• *Workflow Builder:* in a new workflow, add a step → search *Showcase Bot* → *Create a GitHub issue*. Slack runs my code as a step in no-code automations.
• *Scheduled delivery:* `/later 2m hello`. Slack holds the message and delivers it even if this bot is offline."""


@app.action("menu_tour")
def btn_tour(ack, body, client):
    ack()
    client.chat_postMessage(channel=dm(body["user"]["id"]), text=TOUR)


# ---------- Workflow Builder custom step ----------

@app.function("create_github_issue")
def wf_issue(inputs, complete, fail):
    try:
        url = gh.create_issue(inputs["repo"], inputs["title"], inputs.get("body") or "")
        store.bump()
        complete(outputs={"issue_url": url})
    except Exception as e:
        fail(f"Couldn't create the issue: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
