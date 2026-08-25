"""Inventory and remove all artifacts that belong to one configured model."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Iterable

from turbollm.hf_download import configured_path
from turbollm.registry import _hf_cache_path, _legacy_path


@dataclasses.dataclass(frozen=True)
class ModelRemovalTarget:
    path: Path
    size_bytes: int
    is_dir: bool
    label: str


_ARTIFACTS = (
    ("target", "hf_repo", "hf_file", "local_path"),
    ("draft", "draft_hf_repo", "draft_hf_file", "draft_local_path"),
    ("projector", "mmproj_hf_repo", "mmproj_hf_file", "mmproj_local_path"),
)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _artifact_references(model: dict) -> tuple[set[str], set[Path], set[Path]]:
    repos: set[str] = set()
    files: set[Path] = set()
    dirs: set[Path] = set()
    for _label, repo_key, file_key, path_key in _ARTIFACTS:
        repo = model.get(repo_key)
        local = configured_path(model, path_key)
        filename = model.get(file_key)
        if local is not None and filename:
            files.add(local / filename)
        elif local is not None:
            dirs.add(local)
        elif repo:
            repos.add(str(repo))
    return repos, files, dirs


def _path_overlaps_directory(path: Path, directory: Path) -> bool:
    return path == directory or path.is_relative_to(directory)


def gather_removal_targets(
    model: dict,
    other_models: Iterable[dict],
) -> list[ModelRemovalTarget]:
    protected_repos: set[str] = set()
    protected_files: set[Path] = set()
    protected_dirs: set[Path] = set()
    for other in other_models:
        repos, files, dirs = _artifact_references(other)
        protected_repos.update(repos)
        protected_files.update(files)
        protected_dirs.update(dirs)

    result: list[ModelRemovalTarget] = []
    seen: set[Path] = set()
    for label, repo_key, file_key, path_key in _ARTIFACTS:
        repo = model.get(repo_key)
        filename = model.get(file_key)
        local = configured_path(model, path_key)

        candidates: list[tuple[Path, bool]] = []
        protected = False
        if local is not None and filename:
            path = local / filename
            protected = path in protected_files or any(
                _path_overlaps_directory(path, directory)
                for directory in protected_dirs
            )
            candidates.append((path, False))
        elif local is not None:
            protected = any(
                _path_overlaps_directory(path, local)
                for path in protected_files | protected_dirs
            ) or any(
                _path_overlaps_directory(local, directory)
                for directory in protected_dirs
            )
            candidates.append((local, True))
        elif repo:
            protected = str(repo) in protected_repos
            candidates.extend([
                (_hf_cache_path(str(repo)), True),
                (_legacy_path(str(repo)), True),
            ])

        if protected:
            continue
        for path, is_dir in candidates:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            result.append(
                ModelRemovalTarget(
                    path=path,
                    size_bytes=_size(path),
                    is_dir=is_dir,
                    label=label,
                )
            )
    return result


def delete_removal_targets(
    targets: Iterable[ModelRemovalTarget],
) -> tuple[int, int]:
    deleted = 0
    freed = 0
    for target in targets:
        try:
            if target.is_dir:
                shutil.rmtree(target.path)
            else:
                target.path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        freed += target.size_bytes
    return deleted, freed
