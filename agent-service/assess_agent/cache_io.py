"""Compact compressed recovery files, with compatibility for older JSON caches."""
import gzip
import json
from pathlib import Path


def cache_path(path: Path) -> Path:
    compressed = path.with_name(path.name + '.gz')
    return compressed if compressed.is_file() else path


def read_cache_json(path: Path):
    path = cache_path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    try:
        with opener(path, 'rt', encoding='utf-8') as stream:
            return json.load(stream)
    except EOFError as error:
        raise ValueError('Incomplete compressed recovery file') from error


def write_cache_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = path.with_name(path.name + '.gz')
    temporary = compressed.with_name(compressed.name + '.tmp')
    try:
        with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=1) as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
        temporary.replace(compressed)
        path.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
