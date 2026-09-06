"""Repair public, tracked code after an update with an overly restrictive umask.

Runtime data, credentials, .git and untracked files are never traversed.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def repair(root: Path) -> None:
    root = root.resolve()
    tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'])
    directories = {root}
    for raw in tracked.split(b'\0'):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if relative.parts[0] in {'.git', 'data', 'backups', '.venv'} or relative.name == '.env':
            continue
        target = root / relative
        if target.is_symlink() or not target.is_file():
            continue
        # Do not follow symlinked parent directories into runtime storage.
        parents = list(target.parents)
        parents = parents[:parents.index(root) + 1]
        if any(parent.is_symlink() for parent in parents):
            continue
        target.chmod(stat.S_IMODE(target.stat().st_mode) | 0o444)
        directories.update(parents)
    for directory in directories:
        directory.chmod(stat.S_IMODE(directory.stat().st_mode) | 0o555)


if __name__ == '__main__':
    repair(Path(os.environ.get('ENGLISH_LAB_APP_DIR', Path(__file__).resolve().parent.parent)))
