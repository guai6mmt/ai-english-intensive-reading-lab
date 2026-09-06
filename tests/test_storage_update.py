import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from backup_data import backup
from storage_config import paths


def clear_paths(monkeypatch):
    for key in ('ENGLISH_LAB_DATA_DIR', 'MEDIA_STORAGE_ROOT', 'MEDIA_IMPORT_ROOT', 'BACKUP_DIR'):
        monkeypatch.delenv(key, raising=False)


def test_reinstall_keeps_existing_paths_and_quoted_env(tmp_path, monkeypatch):
    clear_paths(monkeypatch)
    (tmp_path / 'data').mkdir()
    assert paths(tmp_path, installing=True)['ENGLISH_LAB_DATA_DIR'] == tmp_path / 'data'
    (tmp_path / '.env').write_text('ENGLISH_LAB_DATA_DIR="saved articles"\nMEDIA_STORAGE_ROOT=original audio\n', encoding='utf-8')
    resolved = paths(tmp_path, installing=True)
    assert resolved['ENGLISH_LAB_DATA_DIR'] == tmp_path / 'saved articles'
    assert resolved['MEDIA_STORAGE_ROOT'] == tmp_path / 'original audio'


def test_full_backup_keeps_articles_audio_database_and_key(tmp_path, monkeypatch):
    clear_paths(monkeypatch)
    data = tmp_path / 'data'
    media = tmp_path / 'external audio'
    imports = data / 'import'
    imports.mkdir(parents=True)
    media.mkdir()
    (data / 'library.json').write_text('{"sources":[]}', encoding='utf-8')
    (data / 'app.db').write_bytes(b'existing database')
    (data / '.settings.key').write_bytes(b'existing key')
    (media / 'original.mp3').write_bytes(b'original recording')
    (tmp_path / '.env').write_text(f'MEDIA_STORAGE_ROOT="{media}"\n', encoding='utf-8')
    target = backup(tmp_path)
    with tarfile.open(target) as archive:
        names = archive.getnames()
        assert 'store-0/library.json' in names
        assert archive.extractfile('store-0/app.db').read() == b'existing database'
        assert archive.extractfile('store-0/.settings.key').read() == b'existing key'
        assert archive.extractfile('store-1/original.mp3').read() == b'original recording'
        manifest = json.load(archive.extractfile('manifest.json'))
        assert manifest['paths']['MEDIA_STORAGE_ROOT'] == str(media)
        assert 'config/.env' in names
    monkeypatch.setenv('BACKUP_DIR', str(data / 'backup'))
    with pytest.raises(RuntimeError):
        backup(tmp_path)
