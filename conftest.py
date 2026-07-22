"""Repo-root conftest: make the suite runnable from a clean clone.

Several modules read ``config/app_config.py`` and ``config/search_config.yaml``
at *import* time (e.g. ``config.logger_config`` → the Telegram error sink reads
``SEARCH_CONFIG_FILE``). Both files are gitignored, so on a fresh clone or in CI
they are absent and pytest can't even collect the suite. Seed them from
``examples/`` when missing — this runs at conftest import, before any test module
is imported. An existing real config is never overwritten.
"""

import shutil
from pathlib import Path

_ROOT = Path(__file__).parent


def _seed_if_missing(example_rel: str, target_rel: str) -> None:
    example = _ROOT / example_rel
    target = _ROOT / target_rel
    if example.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, target)


_seed_if_missing("examples/config/app_config.py", "config/app_config.py")
_seed_if_missing("examples/config/search_config.yaml", "config/search_config.yaml")
