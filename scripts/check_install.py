"""Exercise an installed wheel outside the checkout, including a legacy migration."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    release = version("gh-maintainer")
    wheel = next((root / "dist").glob(f"gh_maintainer-{release}-*.whl"))
    with tempfile.TemporaryDirectory(prefix="gh-maintainer-installed-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment)], check=True
        )
        executable = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(executable), str(wheel)],
            check=True,
        )

        def command(*args: str) -> str:
            return subprocess.run(
                [str(executable), "-I", "-m", "gh_maintainer", *args],
                cwd=work,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        assert release in command("--version")
        db = work / "index.sqlite"
        command("--db", str(db), "demo")
        item = json.loads(
            command("--db", str(db), "show", "example/orbit#42", "--format", "json")
        )["item"]
        command(
            "--db",
            str(db),
            "summarize",
            item["id"],
            "Installed-wheel smoke check",
            "--revision",
            str(item["revision"]),
        )
        command(
            "--db",
            str(db),
            "mark-processed",
            item["id"],
            "--revision",
            str(item["revision"]),
            "--actor",
            "installed-smoke",
        )
        assert (
            json.loads(
                command("--db", str(db), "show", item["id"], "--format", "json")
            )["item"]["processed_by"]
            == "installed-smoke"
        )
        command("skills", "--install", str(work / "skills"))
        assert (work / "skills/gh-maintainer-triage/SKILL.md").is_file()
        policy = work / "policy.json"
        command("policy", "--repo", "example/orbit", "--file", str(policy), "--init")
        command("policy", "--repo", "example/orbit", "--file", str(policy))
        legacy = work / "legacy.sqlite"
        with sqlite3.connect(legacy) as connection:
            connection.executescript((root / "tests/fixtures/legacy.sql").read_text())
            connection.executemany(
                "INSERT INTO metadata VALUES(?,?)",
                [("owner", "owner"), ("owner_id", "77")],
            )
            connection.execute(
                "INSERT INTO repos(id,name,url,archived,raw) VALUES(?,?,?,?,?)",
                (
                    "1",
                    "owner/project",
                    "https://github.com/owner/project",
                    0,
                    '{"id":1,"full_name":"owner/project","html_url":"https://github.com/owner/project","owner":{"login":"owner"}}',
                ),
            )
            raw = {
                "node_id": "LEGACY_1",
                "number": 1,
                "state": "open",
                "title": "Retained report",
                "html_url": "https://github.com/owner/project/issues/1",
                "updated_at": "2026-10-01T00:00:00Z",
            }
            connection.execute(
                "INSERT INTO items(id,repo_id,number,kind,state,title,url,"
                "updated_at,raw) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "LEGACY_1",
                    "1",
                    1,
                    "issue",
                    "open",
                    raw["title"],
                    raw["html_url"],
                    raw["updated_at"],
                    json.dumps(raw),
                ),
            )
            connection.execute(
                "INSERT INTO triage VALUES(?,?,?,?,?)",
                (
                    "LEGACY_1",
                    "planned",
                    "Preserve installed upgrade note",
                    None,
                    raw["updated_at"],
                ),
            )
        command("--db", str(legacy), "migrate")
        assert json.loads(command("--db", str(legacy), "repos", "--format", "json"))[
            "repos"
        ]
        assert (
            json.loads(
                command("--db", str(legacy), "show", "LEGACY_1", "--format", "json")
            )["item"]["note"]
            == "Preserve installed upgrade note"
        )


if __name__ == "__main__":
    main()
