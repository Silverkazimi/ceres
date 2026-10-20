"""Guard: ceres.bcx_cap must resolve its data file relative to the module's own
location, never to the process working directory (). A deployed service
is started from an arbitrary CWD, and the data file must still be found.

Mutation record: this guard was injected-and-reverted against a deliberately
broken `_TABLE_PATH = Path("work/bcx-max-research/max_bcx.json")` (relative to
CWD, pointing at the non-deployed research copy) -- with the process CWD moved
to an unrelated tmp directory that has no `work/` subtree, the deliberately
broken path raised FileNotFoundError and this test went RED; reverting to the
`Path(__file__).resolve()`-based path made it pass again.
"""

from __future__ import annotations

import importlib
import os
import sys

import ceres.bcx_cap as bcx_cap


def test_module_loads_data_regardless_of_process_cwd(tmp_path, monkeypatch):
    # The deployment condition is about: the process was started from
    # some directory that has no `work/` subtree at all.
    empty_cwd = tmp_path / "nowhere-near-the-repo"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    # Force a fresh read (module-level lru_cache would otherwise mask a CWD bug
    # by reusing a value already loaded under the real repo CWD).
    bcx_cap._load_regimes.cache_clear()
    importlib.reload(bcx_cap)
    bcx_cap._load_regimes.cache_clear()

    # This is the real observable: a known cell resolves correctly even though
    # CWD holds nothing the module could have found a relative path against.
    assert bcx_cap.resolve_bcx_max(1, 1, "common", False) == 505

    # Restore module state for later tests in the same process.
    bcx_cap._load_regimes.cache_clear()
    importlib.reload(bcx_cap)


def test_data_path_resolves_under_ceres_data_not_work():
    # Deleting-the-fixture check: this path must point inside the deployed
    # package (ceres/data/), never at the work/ research scratch tree that is
    # excluded from deployment.
    path_str = str(bcx_cap._TABLE_PATH)
    assert path_str.endswith(os.path.join("ceres", "data", "max_bcx.json")), path_str
    assert "work" not in bcx_cap._TABLE_PATH.parts
    assert bcx_cap._TABLE_PATH.is_file(), (
        f"{bcx_cap._TABLE_PATH} must exist -- it is the module's declared source of truth"
    )
