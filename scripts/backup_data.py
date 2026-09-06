"""Create a full recovery archive while the application service is stopped."""
from __future__ import annotations

import json
import os
import tarfile
from datetime import datetime
from pathlib import Path

from storage_config import paths


def backup(root: Path) -> Path:
    stores = paths(root)
    destination = Path(os.environ.get('BACKUP_DIR', root / 'backups')).resolve()
    roots = list(dict.fromkeys(stores.values()))
    for source in roots:
        if destination == source or source in destination.parents:
            raise RuntimeError('备份目录不能放在需要备份的数据目录内。')
        if not source.is_dir():
            raise RuntimeError(f'数据目录不存在，停止更新以防止连接到空目录：{source}')
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = destination / f'full-{stamp}.tar.gz'
    partial = target.with_suffix('.partial')
    manifest = {'paths': {k: str(v) for k, v in stores.items()}, 'created_at': stamp}
    # Nested directories are already in their parent's archive member.
    unique = [p for p in roots if not any(other in p.parents for other in roots)]
    manifest['archive_roots'] = {f'store-{i}': str(p) for i, p in enumerate(unique)}
    try:
        with partial.open('xb') as stream:
            os.chmod(partial, 0o600)
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                for i, source in enumerate(unique):
                    archive.add(source, arcname=f'store-{i}')
                if (root / '.env').is_file():
                    archive.add(root / '.env', arcname='config/.env')
                import io
                raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o600
                archive.addfile(info, io.BytesIO(raw))
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    # Keep all full backups; deletion is an explicit maintenance decision.
    return target


if __name__ == '__main__':
    print(backup(Path(os.environ.get("ENGLISH_LAB_APP_DIR", Path(__file__).resolve().parent.parent)).resolve()))
