"""Platform defaults and offline database file management."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from platformdirs import user_data_path

from gh_maintainer import cli


def invoke(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr("sys.argv", ["gh-maintainer", *args])
    with pytest.raises(SystemExit) as result:
        cli.main()
    return result.value.code


def test_default_database_uses_platform_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_MAINTAINER_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    expected = user_data_path("gh-maintainer", appauthor=False) / "maintainer.sqlite"
    received = []

    async def run(args: SimpleNamespace) -> int:
        received.append(args.db)
        return 0

    monkeypatch.setattr(cli, "run", run)
    assert invoke(monkeypatch, "db") == 0
    assert received[-1] == str(expected)
    monkeypatch.chdir(tmp_path)
    assert invoke(monkeypatch, "prs") == 0
    assert received[-1] == str(expected)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("--db", "selected.sqlite", "db", "path"),
        ("db", "--db", "selected.sqlite", "path"),
        ("db", "path", "--db", "selected.sqlite"),
    ],
)
def test_path_overrides_environment_without_creating_database(
    args: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_MAINTAINER_DB", "environment.sqlite")
    assert invoke(monkeypatch, *args) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "selected.sqlite")
    assert not list(tmp_path.iterdir())


def test_bare_db_shows_environment_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing" / "index.sqlite"
    monkeypatch.setenv("GH_MAINTAINER_DB", str(path))
    assert invoke(monkeypatch, "db") == 0
    assert capsys.readouterr().out.strip() == str(path)
    assert not path.parent.exists()


def test_delete_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "index.sqlite"
    path.write_bytes(b"preserve even an unreadable database")
    assert invoke(monkeypatch, "--db", str(path), "db", "delete") == 2
    assert path.read_bytes() == b"preserve even an unreadable database"


def test_delete_removes_selected_database_and_sqlite_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "index.sqlite"
    assert invoke(monkeypatch, "--db", str(path), "demo") == 0
    assert path.exists()
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    for sidecar in sidecars:
        sidecar.write_bytes(b"sidecar")
    unrelated = tmp_path / "other.sqlite"
    unrelated.write_bytes(b"keep")
    capsys.readouterr()
    assert invoke(monkeypatch, "db", "delete", "--db", str(path), "--yes") == 0
    assert capsys.readouterr().out.strip() == f"Deleted database: {path}"
    assert not path.exists()
    assert all(not sidecar.exists() for sidecar in sidecars)
    assert unrelated.read_bytes() == b"keep"


def test_delete_missing_database_does_not_create_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "missing" / "index.sqlite"
    for _ in range(2):
        assert invoke(monkeypatch, "--db", str(path), "db", "delete", "--yes") == 0
    assert not path.parent.exists()


def test_delete_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "index.sqlite"
    path.write_bytes(b"keep on failure")

    def denied(_self: Path, **_kwargs: object) -> None:
        raise PermissionError("Deletion denied")

    monkeypatch.setattr(Path, "unlink", denied)
    assert invoke(monkeypatch, "--db", str(path), "db", "delete", "--yes") == 1
    assert "Deletion denied" in capsys.readouterr().err
    assert path.read_bytes() == b"keep on failure"


@pytest.mark.parametrize("args", [("path",), ("delete", "--yes")])
def test_in_memory_database_has_no_manageable_file(
    args: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert invoke(monkeypatch, "--db", ":memory:", "db", *args) == 1
    assert "no file" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
