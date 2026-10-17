"""Policy boundaries are enforced on requested writes without changing other state."""

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gh_maintainer.cli import main
from gh_maintainer.config import PolicyFile, load_policy

from .triage_fixtures import populate


def test_policy_init_show_and_no_overwrite(tmp_path: Path) -> None:
    runner = CliRunner()
    path, db = tmp_path / "policy.json", tmp_path / "unused.sqlite"
    args = ["--db", str(db), "policy", "--repo", "Owner/Project", "--file", str(path)]
    result = runner.invoke(main, [*args, "--init", "--format", "json"])
    assert result.exit_code == 0, result.output
    selected = json.loads(result.stdout)
    assert selected["repo"] == "owner/project"
    assert len(selected["sha256"]) == 64
    before = path.read_bytes()
    assert runner.invoke(main, [*args, "--init"]).exit_code == 1
    assert path.read_bytes() == before and not db.exists()
    path.write_text('{"version": 2, "repositories": {}}')
    assert runner.invoke(main, args).exit_code == 1


def test_policy_rejects_unknown_fields_names_and_repositories(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PolicyFile.model_validate({"repositories": {"owner/../bad": {}}})
    with pytest.raises(ValueError, match="Duplicate"):
        PolicyFile.model_validate(
            {"repositories": {"owner/project": {}, "OWNER/PROJECT": {}}}
        )
    with pytest.raises(ValueError):
        PolicyFile.model_validate(
            {"repositories": {"owner/project": {"allow_everything": True}}}
        )
    path = tmp_path / "policy.json"
    path.write_text('{"repositories": {"owner/project": {}}}')
    with pytest.raises(ValueError, match="No policy"):
        load_policy(path, "other/project")


def test_guarded_optional_updates_and_unchanged_rejected_writes(tmp_path: Path) -> None:
    db, policy = tmp_path / "index.sqlite", tmp_path / "policy.json"
    asyncio.run(populate(db, "prior"))
    policy.write_text(
        json.dumps(
            {
                "repositories": {
                    "owner/project": {
                        "permissions": [
                            "summarize",
                            "classify",
                            "rank",
                            "triage",
                            "mark-processed",
                        ],
                        "labels": {"bug": "Reported malfunction"},
                        "ranking": [{"score": 3, "meaning": "Confirmed user impact"}],
                        "triage": {"waiting": "Needs maintainer input"},
                    }
                }
            }
        )
    )
    runner = CliRunner()

    def call(*args: str):
        return runner.invoke(main, ["--db", str(db), *args])

    before = json.loads(call("show", "I_1", "--format", "json").stdout)["item"]
    for args in [
        ("classify", "I_1", "invented"),
        ("rank", "I_1", "99"),
        ("triage", "I_1", "done"),
    ]:
        assert call(*args, "--policy", str(policy), "--revision", "1").exit_code == 1
    assert json.loads(call("show", "I_1", "--format", "json").stdout)["item"] == before
    note = before["note"] + "\nagent/v1: request maintainer input, source C_1_1."
    for args in [
        ("classify", "I_1", "bug"),
        ("rank", "I_1", "3", "--reason", "Caller rubric"),
        ("triage", "I_1", "waiting", "--note", note),
    ]:
        result = call(*args, "--policy", str(policy), "--revision", "1")
        assert result.exit_code == 0, result.output
    saved = json.loads(call("show", "I_1", "--format", "json").stdout)["item"]
    assert saved["note"] == note and saved["ranking"]["score"] == 3
    assert saved["classification"]["labels"] == ["bug"]
    assert saved["processed_revision"] is None
    policy.write_text('{"repositories":{"owner/project":{"permissions":[]}}}')
    assert (
        call(
            "mark-processed", "I_1", "--policy", str(policy), "--revision", "1"
        ).exit_code
        == 1
    )
