"""Read deployment paths without executing .env or revealing secrets."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def read_env(root: Path) -> dict[str, str]:
    result = {}
    path = root / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and not key.lstrip().startswith('#'):
                result[key.strip()] = value.strip().strip('"\'')
    return result


def paths(root: Path, installing: bool = False) -> dict[str, Path]:
    env = read_env(root)
    # Existing project-local data wins over fresh-install defaults.
    old_data = root / 'data'
    default_data = old_data if old_data.exists() or not installing else Path('/srv/english-lab/data')
    result = {}
    for key, fallback in [('ENGLISH_LAB_DATA_DIR', default_data),
                          ('MEDIA_STORAGE_ROOT', None), ('MEDIA_IMPORT_ROOT', None)]:
        if fallback is None:
            fallback = result['ENGLISH_LAB_DATA_DIR'] / ('media' if key == 'MEDIA_STORAGE_ROOT' else 'import')
        value = Path(os.environ.get(key) or env.get(key) or fallback)
        result[key] = (value if value.is_absolute() else root / value).resolve()
    return result


if __name__ == '__main__':
    root = Path(os.environ.get("ENGLISH_LAB_APP_DIR", Path(__file__).resolve().parent.parent)).resolve()
    print(paths(root, '--install' in sys.argv)[sys.argv[1]])
