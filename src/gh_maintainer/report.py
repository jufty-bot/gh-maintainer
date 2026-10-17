"""Deterministic maintenance signals, transparent evidence and portable reports."""

from __future__ import annotations

import json
from typing import Any

from gh_maintainer.store import Store
from gh_maintainer.sync import now, parse_date

Json = dict[str, Any]
DAY_SECONDS = 86400


def item_view(row: Json, at: str) -> Json:
    item = dict(row)
    for field in ("raw", "pr", "signals"):
        item[field] = json.loads(item[field]) if item.get(field) else None
    issue, pr, signals = item["raw"], item["pr"] or {}, item["signals"] or {}
    disposition = item.get("disposition") or "inbox"
    if (
        disposition == "snoozed"
        and item.get("snoozed_until")
        and parse_date(item["snoozed_until"]) <= parse_date(at)
    ):
        disposition = "inbox"
    # A local 'done' decision applies to the observed version, not future activity.
    if disposition == "done" and item["updated_at"] > item["triaged_at"]:
        disposition = "inbox"
    item["disposition"] = disposition
    item["labels"] = [
        label["name"] if isinstance(label, dict) else label
        for label in issue.get("labels", [])
    ]
    item["assignees"] = [user["login"] for user in issue.get("assignees", [])]
    item["author"] = (issue.get("user") or {}).get("login", "ghost")
    item["age_days"] = max(
        0,
        int(
            (parse_date(at) - parse_date(item["updated_at"])).total_seconds()
            / DAY_SECONDS
        ),
    )
    item["draft"] = bool(pr.get("draft"))
    item["review"] = signals.get("reviewDecision") or "UNKNOWN"
    commits = signals.get("commits", {}).get("nodes", [])
    rollup = commits[-1].get("commit", {}).get("statusCheckRollup") if commits else None
    item["checks"] = (rollup or {}).get("state", "UNKNOWN")
    item["mergeable"] = signals.get("mergeable", "UNKNOWN")
    signal_head = commits[-1].get("commit", {}).get("oid") if commits else None
    item["signals_stale"] = bool(
        signals
        and (
            item.get("repo_error")
            or (signal_head and signal_head != pr.get("head", {}).get("sha"))
        )
    )
    if item["signals_stale"]:
        item["checks"] = item["review"] = item["mergeable"] = "UNKNOWN"
    item["reasons"] = []
    score = 0
    if item["state"] == "open":
        if item["checks"] in {"FAILURE", "ERROR"}:
            item["reasons"].append("Failing checks")
            score += 80
        if item["mergeable"] == "CONFLICTING":
            item["reasons"].append("Merge conflict")
            score += 70
        if item["review"] == "CHANGES_REQUESTED":
            item["reasons"].append("Changes requested")
            score += 60
        if (
            item["kind"] == "pr"
            and not item["draft"]
            and item["review"] == "REVIEW_REQUIRED"
        ):
            item["reasons"].append("Review needed")
            score += 50
        if item["kind"] == "issue" and not item["assignees"]:
            item["reasons"].append("Unassigned issue")
            score += 20
        if item["age_days"] >= 30:  # noqa: PLR2004
            item["reasons"].append("No item update for 30+ days")
            score += 30
        if item["kind"] == "pr" and item["checks"] == "UNKNOWN":
            item["reasons"].append("Check status unavailable")
    item["score"] = score
    item["actionable"] = (
        item["state"] == "open"
        and bool(item["available"])
        and bool(item["present"])
        and not item["archived"]
        and disposition not in {"snoozed", "done"}
    )
    return item


def snapshot(store: Store, at: str | None = None) -> Json:
    at = at or now()
    repos = [dict(row) for row in store.db.execute("SELECT * FROM repos ORDER BY name")]
    rows = store.db.execute("""SELECT i.*,r.name AS repo,r.archived,r.present,
        r.error AS repo_error,
        t.disposition,t.note,t.snoozed_until,t.updated_at AS triaged_at
        FROM items i JOIN repos r ON r.id=i.repo_id
        LEFT JOIN triage t ON t.item_id=i.id""")
    items = [item_view(dict(row), at) for row in rows]
    items.sort(
        key=lambda item: (
            -item["score"],
            item["updated_at"],
            item["repo"],
            item["number"],
        )
    )
    queue = [item for item in items if item["actionable"]]
    active = [
        item
        for item in items
        if item["state"] == "open" and item["available"] and item["present"]
    ]
    grouped: dict[str, list[Json]] = {}
    for item in items:
        if item["available"]:
            grouped.setdefault(item["repo_id"], []).append(item)
    for repo in repos:
        scoped = grouped.get(repo["id"], [])
        repo["open_issues"] = sum(
            item["kind"] == "issue" and item["state"] == "open" for item in scoped
        )
        repo["open_prs"] = sum(
            item["kind"] == "pr" and item["state"] == "open" for item in scoped
        )
        repo["attention"] = sum(
            item["actionable"] and bool(item["reasons"]) for item in scoped
        )
        repo["raw"] = json.loads(repo["raw"])
    latest = store.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "owner": store.owner(),
        "generated_at": at,
        "latest_run": dict(latest) if latest else None,
        "counts": {
            "repositories": sum(bool(repo["present"]) for repo in repos),
            "indexed_items": len(items),
            "open_issues": sum(item["kind"] == "issue" for item in active),
            "open_prs": sum(item["kind"] == "pr" for item in active),
            "attention": sum(bool(item["reasons"]) for item in queue),
            "incomplete_repositories": sum(
                repo["present"] and (bool(repo["error"]) or not repo["synced_at"])
                for repo in repos
            ),
        },
        "repos": repos,
        "items": items,
    }


def markdown(data: Json) -> str:
    counts = data["counts"]
    lines = [
        f"# Maintenance report: {data['owner'] or 'not synced'}",
        "",
        f"Generated: {data['generated_at']}",
        "",
        f"{counts['repositories']} repositories; "
        f"{counts['indexed_items']} indexed items; "
        f"{counts['attention']} items with attention signals.",
        "",
    ]
    if counts["incomplete_repositories"]:
        lines += [
            f"Coverage incomplete for {counts['incomplete_repositories']} "
            "repositories. "
            "See synchronization status.",
            "",
        ]
    lines += ["## Maintenance queue", ""]
    queue = [item for item in data["items"] if item["actionable"]]
    for item in queue:
        title = item["title"].replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        reasons = ", ".join(item["reasons"]) or "Open item"
        lines.append(
            f"- [{item['repo']}#{item['number']}: {title}]({item['url']}) "
            f"— {reasons}; triage: {item['disposition']}"
        )
    if not queue:
        lines.append("No active items in the maintenance queue.")
    lines += ["", "## Synchronization status", ""]
    for repo in data["repos"]:
        status = repo["error"] or (
            "Indexed" if repo["synced_at"] else "Not indexed yet"
        )
        if not repo["present"]:
            status = "No longer visible in owner inventory; retained locally"
        lines.append(
            f"- {repo['name']}: {status}. "
            f"Last item sync: {repo['synced_at'] or 'never'}.",
        )
    lines += [
        "",
        "Signals describe observations; approval and green checks "
        "do not establish merge safety.",
        "",
    ]
    return "\n".join(lines)
