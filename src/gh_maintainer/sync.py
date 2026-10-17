"""Inventory and incremental synchronization, with atomic repository checkpoints."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from gh_maintainer.github import GitHub, GitHubError
from gh_maintainer.store import Store

Json = dict[str, Any]
OVERLAP = timedelta(minutes=5)
RECONCILE_AFTER = timedelta(days=7)


def now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Use an ISO timestamp including a timezone.")
    return parsed


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def normalize(issue: Json, repo_id: str, old: Json | None = None) -> Json:
    if not all(
        issue.get(key)
        for key in ("node_id", "number", "state", "title", "html_url", "updated_at")
    ):
        raise ValueError("Malformed GitHub issue; refusing to advance checkpoint.")
    if issue["state"] not in {"open", "closed"}:
        raise ValueError("Unknown GitHub item state.")
    return {
        "id": issue["node_id"],
        "repo_id": repo_id,
        "number": issue["number"],
        "kind": "pr" if "pull_request" in issue else "issue",
        "state": issue["state"],
        "title": issue["title"],
        "url": issue["html_url"],
        "updated_at": issue["updated_at"],
        "available": 1,
        "raw": encode(issue),
        "pr": old.get("pr") if old else None,
        "signals": old.get("signals") if old else None,
        "signals_at": old.get("signals_at") if old else None,
    }


def sync_repo(store: Store, github: GitHub, repo: Json, force_full: bool) -> str | None:
    repo_id = str(repo["id"])
    previous = store.repo(repo_id)
    started_at = (
        now()
    )  # Captured BEFORE requests, so concurrent changes are not skipped.
    full = (
        force_full
        or not previous["synced_at"]
        or not previous["reconciled_at"]
        or parse_date(started_at) - parse_date(previous["reconciled_at"])
        >= RECONCILE_AFTER
    )
    query = {"state": "all", "sort": "updated", "direction": "asc", "per_page": "100"}
    if not full:
        since = parse_date(previous["synced_at"]) - OVERLAP
        query["since"] = since.isoformat().replace("+00:00", "Z")
    base = f"/repos/{repo['full_name']}"
    existing = {row["id"]: row for row in store.items(repo_id)}
    rows: dict[str, Json] = {}
    for page in github.pages(f"{base}/issues?{urlencode(query)}"):
        store.renew_lease()
        for issue in page:
            row = normalize(issue, repo_id, existing.get(issue.get("node_id")))
            rows[row["id"]] = row
    if not full:
        # Reviews and checks may change without issue.updated_at moving.
        for item_id, old in existing.items():
            if old["kind"] == "pr" and old["state"] == "open" and old["available"]:
                rows.setdefault(item_id, dict(old))
    open_prs = []
    for row in rows.values():
        if row["kind"] != "pr":
            continue
        store.renew_lease()
        pr = github.request(f"{base}/pulls/{row['number']}").json()
        if not pr.get("node_id") or pr.get("state") not in {"open", "closed"}:
            raise ValueError("Malformed GitHub pull request.")
        row["pr"] = encode(pr)
        row["state"] = "merged" if pr.get("merged_at") else pr["state"]
        row["updated_at"] = max(row["updated_at"], pr["updated_at"])
        if row["state"] == "open":
            open_prs.append(row)
    signals_error = None
    try:
        signals = github.pull_signals(
            [json.loads(row["pr"])["node_id"] for row in open_prs]
        )
        for row in open_prs:
            row["signals"] = encode(signals[json.loads(row["pr"])["node_id"]])
            row["signals_at"] = started_at
    except (GitHubError, ValueError, KeyError) as error:
        # Preserve old signals with their original timestamp; base item data is usable.
        signals_error = f"PR signals incomplete: {error}"
    store.renew_lease()
    store.commit_repo(
        repo_id, list(rows.values()), started_at, bool(full), signals_error
    )
    return signals_error


def synchronize(
    store: Store,
    github: GitHub,
    full: bool = False,
    only: set[str] | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> Json:
    """Index every token-visible owned repository, including forks and archives."""
    with store.sync_lock():
        run_id = store.start_run(now())
        # Renew before each bounded request, even during very large paginated backfills.
        github.before_request = store.renew_lease
        errors: list[str] = []
        synced = 0
        try:
            identity = github.request("/user").json()
            if not identity.get("id") or not identity.get("login"):
                raise GitHubError("GitHub returned an invalid authenticated identity.")
            login = identity["login"]
            store.set_owner(login, str(identity["id"]))
            repos = github.listing(
                "/user/repos?affiliation=owner&sort=full_name&per_page=100"
            )
            if any(not repo.get("owner", {}).get("login") for repo in repos):
                raise GitHubError("GitHub returned an invalid repository inventory.")
            repos = [
                repo
                for repo in repos
                if repo.get("owner", {}).get("login", "").lower() == login.lower()
            ]
            store.discover(repos)
            if only:
                unknown = only - {repo["full_name"] for repo in repos}
                if unknown:
                    raise ValueError(
                        "Requested repositories are not visible in the owned inventory."
                    )
            for repo in repos:
                if only and repo["full_name"] not in only:
                    continue
                progress(f"Syncing {repo['full_name']}")
                try:
                    error = sync_repo(store, github, repo, full)
                    synced += 1
                    if error:
                        errors.append(f"{repo['full_name']}: {error}")
                except (GitHubError, ValueError, KeyError) as error:
                    message = f"{repo['full_name']}: {error}"
                    store.fail_repo(str(repo["id"]), str(error))
                    errors.append(message)
            status = "partial" if errors else "complete"
            store.finish_run(
                run_id, status, now(), github.requests, "\n".join(errors) or None
            )
            return {
                "owner": login,
                "discovered": len(repos),
                "synced": synced,
                "status": status,
                "errors": errors,
                "requests": github.requests,
                "quota": github.quota,
            }
        except BaseException as error:
            store.finish_run(run_id, "failed", now(), github.requests, str(error))
            raise
        finally:
            github.before_request = None
