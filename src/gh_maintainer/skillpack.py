"""Install repository-owned skill resources shipped in the Python distribution."""

from __future__ import annotations

import shutil
from pathlib import Path


def skill_directory() -> Path:
    packaged = Path(__file__).parent / "skills"
    return packaged if packaged.is_dir() else Path(__file__).parents[2] / "skills"


def install_skills(destination: Path) -> list[str]:
    source = skill_directory()
    names = sorted(
        path.name for path in source.iterdir() if (path / "SKILL.md").is_file()
    )
    if not names:
        raise ValueError("This installation contains no skills.")
    if any((destination / name).exists() for name in names):
        raise ValueError("Skill destination already exists; choose a new directory.")
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(source / name, destination / name)
    return names
