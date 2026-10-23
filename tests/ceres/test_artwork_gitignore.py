"""Downloaded game artwork is git-ignored, so a checkout that has fetched it still serves the release.

Ceres downloads each image in ceres/static/assets/sources.json into ceres/static/assets on first use
(ceres/asset_sources.py). The served-version check (routes_board.compute_identity) counts every untracked file
under the shipped paths, honouring .gitignore, as a local change: without these ignore rules every public
checkout would show the amber "Local — unreleased changes" marker as soon as one image had been downloaded."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "ceres" / "static" / "assets"


def test_every_downloadable_artwork_path_is_git_ignored() -> None:
    listed = [f"ceres/static/assets/{row['path']}" for row in json.loads((ASSETS / "sources.json").read_text(encoding="utf-8"))]
    assert listed
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT, input="\n".join(listed) + "\n", capture_output=True, text=True, check=False,
    )
    ignored = set(result.stdout.split())
    assert not [path for path in listed if path not in ignored], [path for path in listed if path not in ignored][:5]


def test_the_shipped_assets_stay_tracked() -> None:
    """The ignore rules cover downloaded artwork only, never the files the repository ships in that folder."""
    tracked = subprocess.run(["git", "ls-files", "ceres/static/assets"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert "ceres/static/assets/sources.json" in tracked
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT, input="\n".join(tracked) + "\n", capture_output=True, text=True, check=False,
    )
    assert result.stdout.split() == []
