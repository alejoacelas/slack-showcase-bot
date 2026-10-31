"""Persistent state in SQLite: to-dos, polls and votes, and which demos each person has tried."""

import json
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "showcase.db"

FEATURES = {
    "slash": "Slash command",
    "poll": "Live poll",
    "todo": "To-do on Home",
    "repo": "Repo card",
    "ask": "Streaming Claude",
    "assistant": "Assistant panel",
    "canvas": "README as Canvas",
    "list": "Slack List export",
    "chart": "Activity chart",
    "schedule": "Scheduled message",
    "persona": "Custom persona",
    "issue": "Message to GitHub issue",
    "summary": "Thread summary",
    "unfurl": "Link unfurl",
    "reaction": "Reaction trigger",
    "workflow": "Workflow Builder step",
}


def conn() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user TEXT, text TEXT, done INTEGER DEFAULT 0, created REAL);
        CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY, creator TEXT, question TEXT, options TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS votes (poll INTEGER, user TEXT, option INTEGER, PRIMARY KEY (poll, user));
        CREATE TABLE IF NOT EXISTS tried (user TEXT, feature TEXT, at REAL, PRIMARY KEY (user, feature));
        CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER);
    """)
    return c


def bump(name: str = "interactions") -> int:
    with conn() as c:
        c.execute("INSERT INTO counters VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET value = value + 1", (name,))
        return c.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]


def counter(name: str = "interactions") -> int:
    with conn() as c:
        row = c.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return row[0] if row else 0


def mark(user: str, feature: str) -> None:
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO tried VALUES (?, ?, ?)", (user, feature, time.time()))
    bump()


def tried(user: str) -> set[str]:
    with conn() as c:
        return {r[0] for r in c.execute("SELECT feature FROM tried WHERE user = ?", (user,))}


def add_todo(user: str, text: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO todos (user, text, created) VALUES (?, ?, ?)", (user, text.strip(), time.time()))


def todos(user: str) -> list[tuple[int, str, int]]:
    with conn() as c:
        return c.execute("SELECT id, text, done FROM todos WHERE user = ? ORDER BY done, created DESC LIMIT 20", (user,)).fetchall()


def set_done(user: str, done_ids: set[int]) -> None:
    with conn() as c:
        for tid, _, _ in todos(user):
            c.execute("UPDATE todos SET done = ? WHERE id = ? AND user = ?", (int(tid in done_ids), tid, user))


def clear_done(user: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM todos WHERE user = ? AND done = 1", (user,))


def new_poll(creator: str, question: str, options: list[str]) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO polls (creator, question, options, created) VALUES (?, ?, ?, ?)",
                        (creator, question, json.dumps(options), time.time()))
        return cur.lastrowid


def poll(pid: int) -> tuple[str, list[str], list[list[str]]]:
    """Question, options, and voters per option."""
    with conn() as c:
        q, opts = c.execute("SELECT question, options FROM polls WHERE id = ?", (pid,)).fetchone()
        opts = json.loads(opts)
        voters = [[] for _ in opts]
        for user, opt in c.execute("SELECT user, option FROM votes WHERE poll = ?", (pid,)):
            voters[opt].append(user)
        return q, opts, voters


def vote(pid: int, user: str, option: int) -> None:
    with conn() as c:
        c.execute("INSERT INTO votes VALUES (?, ?, ?) ON CONFLICT(poll, user) DO UPDATE SET option = excluded.option",
                  (pid, user, option))


def my_polls(user: str) -> list[int]:
    with conn() as c:
        return [r[0] for r in c.execute("SELECT id FROM polls WHERE creator = ? ORDER BY created DESC LIMIT 3", (user,))]
