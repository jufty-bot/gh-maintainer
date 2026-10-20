"""An isolated, disposable portfolio for browser integration tests."""

import asyncio
import copy
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from gh_maintainer.clock import now
from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.models import Item
from gh_maintainer.repositories import CatalogRepository, TriageService
from gh_maintainer.schemas import CommentSnapshot, Disposition
from gh_maintainer.web import create_app

BODY = """## Reproduction

This is **important**. See the [upstream issue](https://github.com/example/orbit).

- [x] Reproduced locally
- [ ] Add regression coverage

```python
print("retry")
```

| Check | Status |
| --- | --- |
| Retry | Failing |

> Keep the original error.

<script>window.markdownExecuted = true</script>
<img src="x" onerror="window.markdownExecuted = true">
[unsafe](javascript:alert(document.domain))
<a href="javascript:alert(1)">Unsafe HTML link</a>
<svg onload="window.markdownExecuted = true"></svg>
<iframe srcdoc="<script>parent.markdownExecuted = true</script>"></iframe>
"""


async def seed(path: Path) -> None:
    database = Database(path)
    await database.initialize()
    await seed_demo(database)
    async with database.sessions.begin() as session:
        item = await session.get(Item, "DEMO_1_42")
        assert item is not None
        item.raw = {**item.raw, "body": BODY}
    catalog = CatalogRepository(database)
    reviewed = next(row for row in await catalog.items("1") if row.number == 36)
    baseline_signals = copy.deepcopy(reviewed.signals)
    baseline_signals["commits"]["nodes"][0]["commit"]["oid"] = "reviewed-head"
    baseline = reviewed.model_copy(
        update={
            "pr": {**reviewed.pr, "head": {"sha": "reviewed-head"}},
            "signals": baseline_signals,
        }
    )
    await catalog.commit_repo("1", [baseline], now(), False, None)
    reviewed = next(row for row in await catalog.items("1") if row.number == 36)
    await TriageService(database).decide(
        reviewed.id,
        Disposition.DONE,
        "Reviewed the previous code; no further action needed.",
        revision=reviewed.revision,
        finish_review=True,
    )
    signals = copy.deepcopy(reviewed.signals)
    signals["commits"]["nodes"][0]["commit"].update(
        {"oid": "new-review-head", "statusCheckRollup": {"state": "FAILURE"}}
    )
    updated = reviewed.model_copy(
        update={
            "pr": {**reviewed.pr, "head": {"sha": "new-review-head"}},
            "signals": signals,
        }
    )
    comments = [
        CommentSnapshot.from_github(
            {
                "node_id": f"FOLLOW_UP_{index}",
                "body": f"Timeout update {index}. "
                "<script>window.activityExecuted = true</script>",
                "user": reviewed.raw.get("user"),
                "html_url": f"{reviewed.url}#issuecomment-{index}",
                "created_at": now().isoformat(),
                "updated_at": now().isoformat(),
            },
            "1",
            36,
            "issue",
        )
        for index in range(25)
    ]
    await catalog.commit_repo("1", [updated], now(), False, None, comments=comments)
    await database.close()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="maintainer-browser-") as directory:
        db_path = Path(directory) / "index.sqlite"
        asyncio.run(seed(db_path))
        uvicorn.run(create_app(str(db_path)), host="127.0.0.1", port=8876)
