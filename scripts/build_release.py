"""Build a versioned skill archive and checksums alongside uv build's artifacts."""

import hashlib
import json
import zipfile
from importlib.metadata import version
from pathlib import Path


def main() -> None:
    release = version("gh-maintainer")
    root = Path(__file__).resolve().parents[1]
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    files = sorted((root / "skills").rglob("*.md"))
    manifest = {
        "cli_version": release,
        "files": {
            str(path.relative_to(root / "skills")): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in files
        },
    }
    with zipfile.ZipFile(
        destination / f"gh-maintainer-skills-{release}.zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        for path in files:
            archive.write(path, path.relative_to(root / "skills"))
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    artifacts = sorted(
        path
        for path in destination.iterdir()
        if release in path.name and path.suffix in {".whl", ".gz", ".zip"}
    )
    (destination / f"SHA256SUMS-{release}.txt").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in artifacts
        )
    )


if __name__ == "__main__":
    main()
