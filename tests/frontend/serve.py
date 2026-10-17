"""An isolated, disposable portfolio for browser integration tests."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from gh_maintainer.database import Database
from gh_maintainer.demo import seed_demo
from gh_maintainer.models import Item
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
    await database.close()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="maintainer-browser-") as directory:
        db_path = Path(directory) / "index.sqlite"
        asyncio.run(seed(db_path))
        uvicorn.run(create_app(str(db_path)), host="127.0.0.1", port=8876)
