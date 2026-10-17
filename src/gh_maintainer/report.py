"""Render portable reports from typed query results; no persistence or SDK access."""

from gh_maintainer.clock import timestamp
from gh_maintainer.schemas import Portfolio


def markdown(data: Portfolio) -> str:
    counts = data.counts
    lines = [
        f"# Maintenance report: {data.owner or 'not synced'}",
        "",
        f"Generated: {timestamp(data.generated_at)}",
        "",
        f"{counts.repositories} repositories; {counts.indexed_items} indexed items; "
        f"{counts.attention} items with attention signals.",
        "",
    ]
    if counts.incomplete_repositories:
        lines += [
            f"Coverage incomplete for {counts.incomplete_repositories} "
            "repositories. See synchronization status.",
            "",
        ]
    lines += ["## Maintenance queue", ""]
    queue = [item for item in data.items if item.actionable]
    for item in queue:
        title = item.title.replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        reasons = ", ".join(item.reasons) or "Open item"
        lines.append(
            f"- [{item.repo}#{item.number}: {title}]({item.url}) "
            f"— {reasons}; triage: {item.disposition.value}"
        )
    if not queue:
        lines.append("No active items in the maintenance queue.")
    lines += ["", "## Synchronization status", ""]
    for repo in data.repos:
        status = repo.error or ("Indexed" if repo.synced_at else "Not indexed yet")
        if not repo.present:
            status = "No longer visible in owner inventory; retained locally"
        last_sync = timestamp(repo.synced_at) if repo.synced_at else "never"
        lines.append(f"- {repo.name}: {status}. Last item sync: {last_sync}.")
    lines += [
        "",
        "Signals describe observations; approval and green checks "
        "do not establish merge safety.",
        "",
    ]
    return "\n".join(lines)
