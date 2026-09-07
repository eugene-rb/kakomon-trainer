"""参照資料（模範解答・採点基準）の解決（SPEC §6.1 questions[].refs）。

``refs`` はテンプレートディレクトリからの相対パスで、ファイルまたはディレクトリを指定できる。
ディレクトリ指定時は直下の ``.pdf`` ``.md`` ``.txt`` を再帰的に列挙する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.models import RefNode

logger = logging.getLogger(__name__)

RefKind = Literal["pdf", "md", "txt"]

#: 参照資料として扱う拡張子
REF_SUFFIXES: dict[str, RefKind] = {".pdf": "pdf", ".md": "md", ".txt": "txt"}


@dataclass
class RefFile:
    """LLM に添付する参照資料 1 件。"""

    path: Path
    kind: RefKind

    @property
    def name(self) -> str:
        return self.path.name

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")


def _is_within(base: Path, target: Path) -> bool:
    """target が base の配下にあるか（パストラバーサル対策）。"""
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return True


def resolve_refs(template_dir: Path, refs: list[str]) -> list[RefFile]:
    """相対パスのリストを実ファイルのリストに解決する。

    - ファイル指定: そのファイル 1 件（対象拡張子のみ）
    - ディレクトリ指定: 配下の対象拡張子を再帰的に列挙（パス順にソート）
    - 存在しないパスは警告ログを出して黙って読み飛ばす（採点自体は続行する）
    """
    base = template_dir.resolve()
    resolved: list[RefFile] = []
    seen: set[Path] = set()

    for ref in refs:
        target = (base / ref).resolve()
        if not _is_within(base, target):
            logger.warning("テンプレートディレクトリの外を参照しています。無視します: %s", ref)
            continue
        if not target.exists():
            logger.warning("参照資料が見つかりません。無視します: %s", target)
            continue
        candidates = (
            sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else [target]
        )
        for path in candidates:
            kind = REF_SUFFIXES.get(path.suffix.lower())
            if kind is None or path in seen:
                continue
            seen.add(path)
            resolved.append(RefFile(path=path, kind=kind))
    return resolved


def build_ref_tree(refs_dir: Path) -> list[RefNode]:
    """``refs/`` 以下のツリーを UI 用に組み立てる（相対パスで返す）。"""
    if not refs_dir.exists():
        return []

    def walk(directory: Path) -> list[RefNode]:
        nodes: list[RefNode] = []
        for path in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name)):
            rel = path.relative_to(refs_dir.parent).as_posix()
            if path.is_dir():
                nodes.append(RefNode(name=path.name, path=rel, is_dir=True, children=walk(path)))
            elif path.suffix.lower() in REF_SUFFIXES:
                nodes.append(RefNode(name=path.name, path=rel, is_dir=False))
        return nodes

    return walk(refs_dir)
