"""Bounded local folder inspection. Events describe observed files, never model guesses."""

import os
from pathlib import Path


MAX_FILES = 400
MAX_BYTES = 20 * 1024 * 1024
SKIP_DIRS = {'.git', '.wiki-optimizer', 'node_modules', '.venv', '__pycache__'}


def inspect_folder(directory):
    root = Path(directory).expanduser().resolve()
    if not directory or not root.is_dir():
        raise ValueError('폴더를 찾을 수 없습니다. 경로를 확인해주세요.')
    source = root / 'raw' if (root / 'raw').is_dir() else root
    yield {'type': 'started', 'root': str(root), 'source': str(source)}
    files, folders, total, visited = [], set(), 0, 0
    for parent, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS
                         and not (Path(parent) / d).is_symlink())
        visited += 1
        if visited > 4000:
            raise ValueError('폴더가 너무 많습니다. 더 작은 문서 폴더를 선택해주세요.')
        for name in sorted(names):
            path = Path(parent) / name
            if path.suffix != '.md' or name.lower() == 'readme.md' or path.is_symlink():
                continue
            size = path.stat().st_size
            total += size
            if len(files) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError('문서는 400개, 총 20MB까지 분석할 수 있습니다. 더 작은 폴더를 선택해주세요.')
            relative = str(path.relative_to(source))
            folders.add(str(path.parent.relative_to(source)))
            item = {'path': str(path), 'name': relative, 'size': size}
            files.append(item)
            yield {'type': 'file', **item, 'count': len(files)}
    if not files:
        raise ValueError('분석할 Markdown 문서가 없습니다. README.md 외의 .md 파일을 넣어주세요.')
    yield {'type': 'complete', 'root': str(root), 'source': str(source), 'files': files,
           'count': len(files), 'folders': len(folders), 'bytes': total,
           'has_wiki': (root / 'raw').is_dir() and (root / 'wiki').is_dir()}
