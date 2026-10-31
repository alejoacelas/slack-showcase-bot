"""Thin GitHub layer over the `gh` CLI, which holds the credentials."""

import base64
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

OWNER = "alejoacelas"
_cache: dict = {}


def api(path: str, method: str = "GET", fields: dict | None = None, ttl: int = 120):
    key = (path, method, json.dumps(fields, sort_keys=True))
    if method == "GET" and key in _cache and time.time() - _cache[key][0] < ttl:
        return _cache[key][1]
    cmd = ["gh", "api", "-X", method, path]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    data = json.loads(out.stdout or "null")
    if method == "GET":
        _cache[key] = (time.time(), data)
    return data


def repos(limit: int = 100) -> list[dict]:
    rs = api(f"/user/repos?per_page={limit}&sort=pushed&affiliation=owner")
    return [r for r in rs if not r.get("archived")]


def repo(name: str) -> dict:
    return api(f"/repos/{OWNER}/{name}")


def commits(name: str, n: int = 5, since: datetime | None = None) -> list[dict]:
    q = f"per_page={n}" + (f"&since={since.strftime('%Y-%m-%dT%H:%M:%SZ')}" if since else "")
    try:
        return api(f"/repos/{OWNER}/{name}/commits?{q}")
    except RuntimeError:
        return []  # empty repos have no commits


def readme(name: str) -> str:
    try:
        return base64.b64decode(api(f"/repos/{OWNER}/{name}/readme")["content"]).decode("utf-8", "replace")
    except RuntimeError:
        return ""


def files(name: str) -> list[str]:
    try:
        tree = api(f"/repos/{OWNER}/{name}/git/trees/HEAD?recursive=1")["tree"]
        return [t["path"] for t in tree if t["type"] == "blob"]
    except RuntimeError:
        return []


def read_file(name: str, path: str) -> str:
    data = api(f"/repos/{OWNER}/{name}/contents/{path}")
    return base64.b64decode(data["content"]).decode("utf-8", "replace")


def open_counts(name: str) -> tuple[int, int]:
    issues = api(f"/repos/{OWNER}/{name}/issues?state=open&per_page=100")
    prs = sum(1 for i in issues if "pull_request" in i)
    return len(issues) - prs, prs


def create_issue(name: str, title: str, body: str = "") -> str:
    return api(f"/repos/{OWNER}/{name}/issues", "POST", {"title": title, "body": body})["html_url"]


def activity(days: int = 14, top: int = 6) -> dict[str, Counter]:
    """Commits per day for the most recently pushed repos."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = {}
    for r in repos()[:top]:
        days_ = Counter(c["commit"]["author"]["date"][:10] for c in commits(r["name"], 100, since))
        if days_:
            out[r["name"]] = days_
    return out


def ago(iso: str) -> str:
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))
    s = int(delta.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return "just now"
