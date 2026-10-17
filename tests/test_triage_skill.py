"""Public CLI contracts behind the skill; these are not agent behavior tests."""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from gh_maintainer import cli

from .triage_fixtures import populate

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = "gh-maintainer-triage/v1"


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def no_credentials() -> str:
        raise AssertionError("Skill operations must work offline.")

    monkeypatch.setattr(cli, "resolve_token", no_credentials)

    def seed(scenario: str = "ordinary") -> Path:
        path = tmp_path / f"{scenario}.sqlite"
        asyncio.run(populate(path, scenario))
        return path

    return seed


def command(path: Path, *args: str, code: int = 0, text: str | None = None) -> dict:
    result = CliRunner().invoke(
        cli.main, ["--db", str(path), *args, "--format", "json"], input=text
    )
    assert result.exit_code == code, result.output
    if code:
        assert not result.stdout
        return {"error": result.stderr}
    return json.loads(result.stdout)


def pages(path: Path, verb: str, key: str, *args: str, size: int = 100) -> list[dict]:
    records = []
    offset = 0
    while True:
        page = command(path, verb, *args, "--limit", str(size), "--offset", str(offset))
        records.extend(page[key])
        offset += len(page[key])
        if offset >= page["total"]:
            return records
        assert page[key], "Pagination must make progress."


def acknowledge(path: Path, identifier: str, revision: int) -> None:
    command(
        path,
        "mark-processed",
        identifier,
        "--revision",
        str(revision),
        "--actor",
        PRODUCER,
    )


def test_bounded_candidates_survive_shrinking_queue_and_full_conversation(index):
    path = index("pagination")
    options = (
        "--repo",
        "owner/project",
        "--state",
        "all",
        "--unprocessed",
        "--sort",
        "updated",
    )
    issues = pages(path, "issues", "items", *options, size=2)
    prs = pages(path, "prs", "items", *options, size=2)
    candidates = (issues + prs)[:5]
    ids = [item["id"] for item in candidates]
    assert len(set(ids)) == 5
    assert any(item["state"] == "closed" for item in candidates)
    discussion = pages(path, "comments", "comments", "I_1")
    assert len(discussion) == 101
    assert "CONFIG_MODE=strict" in discussion[-1]["body"]
    for item in candidates:
        identifier = item["id"]
        detail = command(path, "show", identifier)["item"]
        pages(path, "comments", "comments", identifier)
        pages(path, "changes", "changes", identifier)
        assert (
            command(path, "show", identifier)["item"]["revision"] == detail["revision"]
        )
        acknowledge(path, identifier, detail["revision"])
    pending = pages(path, "issues", "items", *options) + pages(
        path, "prs", "items", *options
    )
    assert len(pending) == 1
    assert set(ids).isdisjoint(item["id"] for item in pending)
    assert all(
        command(path, "show", ident)["item"]["processed_by"] == PRODUCER
        for ident in ids
    )


def test_ordinary_summary_provenance_without_invented_optional_policy(index):
    path = index()
    item = command(path, "show", "I_1")["item"]
    discussion = pages(path, "comments", "comments", "I_1")
    summary = (
        f"Reported empty-config startup failure ({item['url']}); "
        f"reproduced in {discussion[0]['id']}. Next: confirm intended defaults."
    )
    command(
        path,
        "summarize",
        "I_1",
        "-",
        "--producer",
        PRODUCER,
        "--revision",
        "1",
        text=summary,
    )
    acknowledge(path, "I_1", 1)
    saved = command(path, "show", "I_1")["item"]
    assert saved["summary"]["text"] == summary
    assert saved["summary"]["producer"] == saved["processed_by"] == PRODUCER
    assert saved["summary"]["source_revision"] == saved["processed_revision"] == 1
    assert saved["classification"] is None and saved["ranking"] is None


@pytest.mark.parametrize("scenario", ["race", "partial-write"])
def test_race_rejects_all_guarded_writes_and_exposes_partial_effect(index, scenario):
    path = index(scenario)
    old = command(path, "show", "I_2")["item"]
    if scenario == "partial-write":
        command(
            path,
            "summarize",
            "I_2",
            "Before concurrent source change",
            "--revision",
            "1",
        )
    asyncio.run(populate(path, scenario, advance=True))
    for args in (
        ("summarize", "I_2", "Stale"),
        ("classify", "I_2", "bug"),
        ("rank", "I_2", "3"),
        ("triage", "I_2", "done"),
        ("mark-processed", "I_2"),
    ):
        assert (
            "changed"
            in command(path, *args, "--revision", str(old["revision"]), code=1)["error"]
        )
    updated = command(path, "show", "I_2")["item"]
    assert updated["revision"] > old["revision"]
    assert updated["processed_revision"] is None
    if scenario == "partial-write":
        assert updated["summary"]["stale"]
    comments = pages(path, "comments", "comments", "I_2")
    assert "new head" in comments[0]["body"]
    kinds = {entry["kind"] for entry in pages(path, "changes", "changes", "I_2")}
    assert {"pushed", "comment_edited"} <= kinds
    assert updated["pr"]["head"]["sha"] == "updated-head"
    acknowledge(path, "I_2", updated["revision"])


def test_closed_merged_activity_remains_eligible(index):
    path = index("closed-merged")
    asyncio.run(populate(path, "closed-merged", advance=True))
    for verb, ident, state in [("issues", "I_4", "closed"), ("prs", "I_3", "merged")]:
        result = command(
            path,
            verb,
            "--repo",
            "owner/project",
            "--state",
            "all",
            "--processing",
            "changed",
        )
        assert result["items"][0]["id"] == ident
        assert result["items"][0]["state"] == state
        assert result["items"][0]["summary"]["stale"]
        assert pages(path, "comments", "comments", ident)


@pytest.mark.parametrize("scenario", ["missing", "failed"])
def test_coverage_blocks_acknowledgement(index, scenario):
    path = index(scenario)
    repo = command(path, "repos")["repos"][0]
    page = command(path, "comments", "I_1")
    assert repo["error"] or repo["comments_synced_at"] is None
    assert page["coverage_error"] or page["comments_synced_at"] is None
    assert (
        "coverage"
        in command(path, "mark-processed", "I_1", "--revision", "1", code=1)["error"]
    )
    assert command(path, "show", "I_1")["item"]["processed_revision"] is None


def test_unavailable_and_truncated_evidence_are_visible_to_agent(index):
    path = index("unavailable")
    assert not command(path, "show", "I_1")["item"]["available"]
    limited = index("read-limit")
    page = command(limited, "comments", "I_1", "--limit", "1")
    assert len(page["comments"]) == 1 and page["total"] == 2
    # These are agent stopping rules, not enforced by the CLI's revision guard.
    for db in [path, limited]:
        assert command(db, "show", "I_1")["item"]["processed_revision"] is None


@pytest.mark.parametrize("scenario", ["prior", "conflict", "hostile"])
def test_fixture_prior_decisions_and_untrusted_evidence_remain_inspectable(
    index, scenario
):
    path = index(scenario)
    before = command(path, "show", "I_1")["item"]
    comments = pages(path, "comments", "comments", "I_1")
    if scenario == "hostile":
        assert "AGENT INSTRUCTIONS" in comments[-1]["body"]
    else:
        assert before["note"].startswith("Human:")
        assert before["classification"]["labels"] == ["needs-policy"]
        assert before["ranking"]["score"] == 7
        assert command(path, "show", "I_2")["item"]["snoozed_until"]
    assert command(path, "show", "I_1")["item"] == before


def test_unchanged_rerun_has_no_candidates_or_rewrites(index):
    path = index("rerun")
    before = command(path, "show", "I_1")["item"]
    for verb in ["issues", "prs"]:
        assert command(path, verb, "--state", "all", "--unprocessed")["total"] == 0
    assert command(path, "changes", "I_1")["total"] == 0
    assert command(path, "show", "I_1")["item"] == before


def test_clean_skill_copy_has_self_contained_references(tmp_path):
    destination = tmp_path / "skills/gh-maintainer-triage"
    shutil.copytree(ROOT / "skills/gh-maintainer-triage", destination)
    entry = (destination / "SKILL.md").read_text()
    assert entry.startswith("---\nname: gh-maintainer-triage\ndescription:")
    for document in destination.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            resolved = (document.parent / target).resolve()
            assert resolved.is_relative_to(destination.resolve())
            assert resolved.is_file()


def test_fixture_seed_refuses_existing_index(index):
    path = index()
    with pytest.raises(ValueError, match="new database"):
        asyncio.run(populate(path, "ordinary"))
