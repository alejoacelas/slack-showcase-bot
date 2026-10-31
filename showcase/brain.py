"""Claude with tools over Alejo's GitHub, streaming its answer as it writes."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

import anthropic

from showcase import gh

MODEL = os.environ.get("SHOWCASE_MODEL", "claude-opus-5-5")
client = anthropic.Anthropic()

SYSTEM = f"""You are Showcase Bot, Alejo's assistant inside Slack. You can look through Alejo's GitHub repositories (owner `{gh.OWNER}`) with your tools, and open issues when Alejo asks for one.

Write for Slack: short paragraphs, bullets where they help, standard Markdown, links to repos, files and commits on github.com. Lead with the answer. Today is {{today}}."""

TOOLS = [
    {"name": "list_repos", "description": "Alejo's repos, most recently pushed first, with description, visibility and last push.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "recent_activity", "description": "Commit messages across all repos pushed in the last N days.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 7}}}},
    {"name": "repo_overview", "description": "A repo's description, README (truncated) and file list.",
     "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]}},
    {"name": "read_file", "description": "Read one file from a repo's default branch (truncated to 12k characters).",
     "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "path": {"type": "string"}}, "required": ["repo", "path"]}},
    {"name": "create_issue", "description": "Open a GitHub issue. Only when Alejo explicitly asks to create or file one.",
     "input_schema": {"type": "object", "properties": {"repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}},
                      "required": ["repo", "title", "body"]}},
]

STATUS = {
    "list_repos": "is listing your repos…",
    "recent_activity": "is reading this week's commits…",
    "repo_overview": "is reading the README…",
    "read_file": "is opening a file…",
    "create_issue": "is filing the issue…",
}


def run_tool(name: str, args: dict) -> str:
    if name == "list_repos":
        return json.dumps([{"name": r["name"], "private": r["private"], "description": r["description"],
                            "pushed": gh.ago(r["pushed_at"])} for r in gh.repos()[:60]])
    if name == "recent_activity":
        since = datetime.now(timezone.utc) - timedelta(days=args.get("days", 7))
        out = {}
        for r in gh.repos():
            if datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) < since:
                break
            msgs = [c["commit"]["message"].splitlines()[0] for c in gh.commits(r["name"], 30, since)]
            if msgs:
                out[r["name"]] = msgs
        return json.dumps(out)
    if name == "repo_overview":
        r = gh.repo(args["repo"])
        return json.dumps({"description": r["description"], "private": r["private"], "url": r["html_url"],
                           "readme": gh.readme(args["repo"])[:6000], "files": gh.files(args["repo"])[:300]})
    if name == "read_file":
        return gh.read_file(args["repo"], args["path"])[:12000]
    if name == "create_issue":
        return gh.create_issue(args["repo"], args["title"], args["body"])
    return "unknown tool"


def answer(messages: list[dict], on_text: Callable[[str], None], on_status: Callable[[str], None] = lambda s: None,
           tools: bool = True) -> str:
    """Run the tool loop; stream text through on_text. Returns the full text."""
    system = SYSTEM.format(today=datetime.now().strftime("%A %d %B %Y"))
    full = ""
    for _ in range(8):
        kwargs = {"tools": TOOLS} if tools else {}
        with client.messages.stream(model=MODEL, max_tokens=4000, system=system, messages=messages, **kwargs) as stream:
            for text in stream.text_stream:
                full += text
                on_text(text)
            msg = stream.get_final_message()
        if msg.stop_reason != "tool_use":
            return full
        messages = messages + [{"role": "assistant", "content": msg.content}]
        results = []
        for block in msg.content:
            if block.type == "tool_use":
                on_status(STATUS.get(block.name, "is working…"))
                try:
                    out = run_tool(block.name, block.input)
                except Exception as e:
                    out = f"error: {e}"
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages = messages + [{"role": "user", "content": results}]
    return full


def summarize(text: str) -> str:
    r = client.messages.create(
        model=MODEL, max_tokens=1000,
        system="Summarize this Slack conversation in 3 short bullets: what was discussed, what was decided, and open questions. Use Slack mrkdwn (*bold*, • bullets).",
        messages=[{"role": "user", "content": text}],
    )
    return "".join(b.text for b in r.content if b.type == "text")
