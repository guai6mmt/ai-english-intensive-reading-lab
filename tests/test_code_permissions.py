import os
import stat
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_code_permissions as permissions


def test_only_tracked_public_code_is_repaired(tmp_path, monkeypatch):
    package = tmp_path / 'english_lab'
    package.mkdir()
    code = package / 'sentences.py'
    code.write_text('pass')
    secret = tmp_path / '.env'
    secret.write_text('private')
    data = tmp_path / 'data'
    data.mkdir()
    audio = data / 'original.mp3'
    audio.write_bytes(b'audio')
    untracked = tmp_path / 'private.txt'
    untracked.write_text('private')
    monkeypatch.setattr(permissions.subprocess, 'check_output', lambda *a, **k: b'english_lab/sentences.py\0.env\0data/original.mp3\0')
    changes = {}
    monkeypatch.setattr(Path, 'chmod', lambda path, mode: changes.update({path: mode}))
    permissions.repair(tmp_path)
    assert changes[code] & 0o444 == 0o444
    assert changes[package] & 0o555 == 0o555
    assert changes[tmp_path] & 0o555 == 0o555
    assert secret not in changes and audio not in changes and untracked not in changes


def test_update_scopes_private_umask_to_backup():
    script = (Path(__file__).resolve().parents[1] / 'scripts/server_safe_update.sh').read_text(encoding='utf-8')
    assert '\numask 022\n' in script
    assert '\numask 077\n' not in script
    assert '(umask 077; "$PYTHON_BIN" "$HELPERS/backup_data.py")' in script
    assert script.count('"$PYTHON_BIN" "$HELPERS/repair_code_permissions.py"') == 3


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission regression runs on Linux CI')
def test_restrictive_checkout_modes_are_repaired(tmp_path, monkeypatch):
    package = tmp_path / 'english_lab'
    package.mkdir(mode=0o700)
    code = package / 'sentences.py'
    code.write_text('pass')
    code.chmod(0o600)
    secret = tmp_path / '.env'
    secret.write_text('private')
    secret.chmod(0o600)
    monkeypatch.setattr(permissions.subprocess, 'check_output', lambda *a, **k: b'english_lab/sentences.py\0')
    permissions.repair(tmp_path)
    assert stat.S_IMODE(code.stat().st_mode) == 0o644
    assert stat.S_IMODE(package.stat().st_mode) == 0o755
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
