"""Offline sample portfolio for trying the dashboard without GitHub credentials."""

from __future__ import annotations

from datetime import timedelta

from gh_maintainer.store import Store
from gh_maintainer.sync import encode, normalize, now, parse_date

MERGED_PR = 36


def seed_demo(store: Store) -> None:
    if store.db.execute("SELECT COUNT(*) FROM repos").fetchone()[0]:
        raise ValueError("Demo requires an empty database; use --db .data/demo.sqlite.")
    at = now()
    store.set_owner("example")
    repos = [
        {
            "id": index,
            "full_name": f"example/{name}",
            "html_url": f"https://github.com/example/{name}",
            "description": description,
            "archived": False,
            "private": name == "infrastructure",
            "owner": {"login": "example"},
            "language": language,
        }
        for index, (name, description, language) in enumerate(
            [
                ("orbit", "An API for small, connected services", "Python"),
                ("field-notes", "A shared notebook for the internet", "TypeScript"),
                ("infrastructure", "Configuration for the things that run", "HCL"),
            ],
            1,
        )
    ]
    store.discover(repos)
    samples = [
        (
            1,
            42,
            "pr",
            "Upgrade HTTP client and retry transient failures",
            2,
            "FAILURE",
            "APPROVED",
        ),
        (
            1,
            39,
            "issue",
            "Requests hang when the upstream connection closes",
            34,
            None,
            None,
        ),
        (
            2,
            18,
            "pr",
            "Add keyboard shortcuts to the editor",
            4,
            "SUCCESS",
            "REVIEW_REQUIRED",
        ),
        (2, 15, "issue", "Support importing Markdown notebooks", 8, None, None),
        (
            3,
            7,
            "pr",
            "Rotate service account permissions",
            1,
            "PENDING",
            "CHANGES_REQUESTED",
        ),
        (1, 36, "pr", "Document authentication options", 7, "SUCCESS", "APPROVED"),
    ]
    for repo in repos:
        rows = []
        for repo_id, number, kind, title, days, checks, review in samples:
            if repo_id != repo["id"]:
                continue
            updated = (
                (parse_date(at) - timedelta(days=days))
                .isoformat()
                .replace("+00:00", "Z")
            )
            issue = {
                "node_id": f"DEMO_{repo_id}_{number}",
                "number": number,
                "title": title,
                "state": "closed" if number == MERGED_PR else "open",
                "updated_at": updated,
                "html_url": (
                    f"{repo['html_url']}/"
                    f"{'pull' if kind == 'pr' else 'issues'}/{number}"
                ),
                "labels": [{"name": "bug" if kind == "issue" else "maintenance"}],
                "user": {"login": "contributor"},
                "assignees": [],
                "body": "Demo item. All data in this database is synthetic.",
            }
            if kind == "pr":
                issue["pull_request"] = {}
            row = normalize(issue, str(repo_id))
            if kind == "pr":
                row["pr"] = encode(
                    {"node_id": f"PR_{repo_id}_{number}", "draft": False}
                )
                row["signals"] = encode(
                    {
                        "reviewDecision": review,
                        "mergeable": "MERGEABLE",
                        "commits": {
                            "nodes": [
                                {"commit": {"statusCheckRollup": {"state": checks}}}
                            ]
                        },
                    }
                )
                row["signals_at"] = at
                if number == MERGED_PR:
                    row["state"] = "merged"
            rows.append(row)
        store.commit_repo(str(repo["id"]), rows, at, True, None)
    run_id = store.start_run(at)
    store.finish_run(run_id, "complete", at, 0)
