#!/usr/bin/env python3
"""Import an offline English→Chinese dictionary into the local database.

The lookup panel checks this table before falling back to an online model or a
translation API, so importing a word list makes word lookup work fully offline.

Recommended source — ECDICT (free, ~770k words):
    https://github.com/skywind3000/ECDICT  (download ``ecdict.csv``)

Usage (from the project root, with the virtualenv active):
    python scripts/import_dictionary.py path/to/ecdict.csv
    python scripts/import_dictionary.py words.csv --source my-list
    python scripts/import_dictionary.py words.csv --no-header   # plain: word,translation

The importer auto-detects ECDICT-style headers (word, phonetic, definition,
translation, ...) and generic ``word,translation`` files. Re-running updates
existing entries in place.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterator
from pathlib import Path

# Allow running the script directly (``python scripts/import_dictionary.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep progress output legible on a Windows GBK console (word lists and paths may
# contain non-ASCII characters that the default codepage cannot encode).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from english_lab.database import initialize_database  # noqa: E402
from english_lab.vocabulary import bulk_upsert_dictionary, dictionary_stats  # noqa: E402

# csv fields can be very large (ECDICT packs long definitions into one cell).
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

WORD_KEYS = ("word", "term", "headword", "vocabulary", "单词", "词条")
TRANSLATION_KEYS = ("translation", "trans", "chinese", "meaning", "释义", "中文", "解释")
DEFINITION_KEYS = ("definition", "def", "english", "英文", "英文释义")
PHONETIC_KEYS = ("phonetic", "phon", "ipa", "音标")
POS_KEYS = ("part_of_speech", "pos_tag", "词性")


def _clean(value: str | None) -> str:
    """Collapse the literal and real newlines ECDICT uses inside one cell."""
    text = (value or "").strip()
    return text.replace("\\n", "; ").replace("\r\n", "; ").replace("\n", "; ").strip()


def _pick(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {name.lower(): name for name in header}
    for key in candidates:
        if key in lowered:
            return lowered[key]
    return None


def _looks_like_header(row: list[str]) -> bool:
    return bool(row) and row[0].strip().lower() in WORD_KEYS


def _rows(path: Path, source: str, has_header: bool | None) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            first = next(reader)
        except StopIteration:
            return
        header_present = _looks_like_header(first) if has_header is None else has_header
        if header_present:
            header = [name.strip() for name in first]
            word_col = _pick(header, WORD_KEYS) or header[0]
            translation_col = _pick(header, TRANSLATION_KEYS)
            definition_col = _pick(header, DEFINITION_KEYS)
            phonetic_col = _pick(header, PHONETIC_KEYS)
            pos_col = _pick(header, POS_KEYS)
            if translation_col is None and definition_col is None and len(header) > 1:
                translation_col = header[1]
            for values in reader:
                record = dict(zip(header, values))
                yield {
                    "term": record.get(word_col, ""),
                    "translation": _clean(record.get(translation_col)) if translation_col else "",
                    "definition": _clean(record.get(definition_col)) if definition_col else "",
                    "phonetic": _clean(record.get(phonetic_col)) if phonetic_col else "",
                    "part_of_speech": _clean(record.get(pos_col)) if pos_col else "",
                    "source": source,
                }
        else:
            # Headerless: word[,translation[,phonetic]]
            for values in ([first] + list(reader)):
                if not values:
                    continue
                yield {
                    "term": values[0],
                    "translation": _clean(values[1]) if len(values) > 1 else "",
                    "phonetic": _clean(values[2]) if len(values) > 2 else "",
                    "source": source,
                }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an offline dictionary (e.g. ECDICT CSV).")
    parser.add_argument("path", help="Path to the dictionary CSV file")
    parser.add_argument("--source", default="", help="Label stored with each row (default: file stem)")
    parser.add_argument("--no-header", dest="header", action="store_false", default=None,
                        help="Treat the file as headerless word,translation rows")
    args = parser.parse_args()

    path = Path(args.path).expanduser()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    source = args.source or path.stem
    initialize_database()

    total = 0

    def counting(rows: Iterator[dict[str, str]]) -> Iterator[dict[str, str]]:
        nonlocal total
        for row in rows:
            total += 1
            if total % 50000 == 0:
                print(f"  ...read {total:,} rows")
            yield row

    print(f"Importing {path.name} (source={source}) ...")
    written = bulk_upsert_dictionary(counting(_rows(path, source, args.header)))
    stats = dictionary_stats()
    print(f"Done. Imported/updated {written:,} entries from {total:,} rows.")
    print(f"Offline dictionary now holds {stats['total']:,} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
