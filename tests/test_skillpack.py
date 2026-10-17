"""Installed skill resources remain complete and never overwrite prior work."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from gh_maintainer import cli
from gh_maintainer.skillpack import install_skills, skill_directory


def test_complete_bundle_install_and_collision_preflight(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    target = existing / "gh-maintainer-triage"
    target.mkdir()
    (target / "custom.md").write_text("Keep human edits")
    with pytest.raises(ValueError, match="already exists"):
        install_skills(existing)
    assert list(existing.iterdir()) == [target]
    assert (target / "custom.md").read_text() == "Keep human edits"
    destination = tmp_path / "new"
    names = install_skills(destination)
    assert "gh-maintainer-review" in names and "gh-maintainer-fix" in names
    for source in skill_directory().rglob("*.md"):
        assert (
            destination / source.relative_to(skill_directory())
        ).read_bytes() == source.read_bytes()


def test_skill_command_does_not_initialize_database(tmp_path: Path) -> None:
    db = tmp_path / "unused.sqlite"
    result = CliRunner().invoke(
        cli.main,
        [
            "--db",
            str(db),
            "skills",
            "--install",
            str(tmp_path / "skills"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not db.exists()
