"""ファイル名の共通部分と末尾の役割から、用紙と参照資料を対応付ける。"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class RegistrationPreviewRequest(BaseModel):
    filenames: list[str] = Field(min_length=1, max_length=200)


class RegistrationGroup(BaseModel):
    title: str
    sheet: str
    references: list[str] = Field(default_factory=list)


_ROLE = re.compile(r"^(.+)_(sheet|answers|rubric)$", re.IGNORECASE)


def plan_registration(filenames: list[str]) -> list[RegistrationGroup]:
    """推測による紐付けはしない。対応不能・重複は登録前にまとめて知らせる。"""
    groups: dict[str, dict] = {}
    seen: set[str] = set()
    errors: list[str] = []
    for filename in filenames:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename):
            errors.append(f"ファイル名は半角英数字・_・-・. で指定してください（例: kyodai_english_2024_sheet.pdf）: {filename}")
            continue
        normalized = filename
        if normalized.casefold() in seen:
            errors.append(f"同じファイル名が複数あります: {filename}")
            continue
        seen.add(normalized.casefold())
        suffix = Path(normalized).suffix.lower()
        stem = Path(normalized).stem
        match = _ROLE.fullmatch(stem)
        title, role = match.groups() if match else (stem, "sheet")
        title = title.strip(" _-")
        is_sheet = role.lower() == "sheet"
        if not title or suffix not in ({".pdf"} if is_sheet else {".pdf", ".md", ".txt"}):
            errors.append(f"用紙は *_sheet.pdf、解説は *_answers.pdf、採点基準は *_rubric.pdf で指定してください（資料は .md / .txt も可）: {filename}")
            continue
        key = title.casefold()
        group = groups.setdefault(key, {"title": title, "sheets": [], "references": []})
        group["sheets" if is_sheet else "references"].append(filename)
    result = []
    for group in groups.values():
        if len(group["sheets"]) != 1:
            errors.append(f"{group['title']}: 解答用紙が{'ありません' if not group['sheets'] else '複数あります'}。共通名と末尾の「_sheet」を確認してください。")
            continue
        result.append(RegistrationGroup(title=group["title"], sheet=group["sheets"][0], references=group["references"]))
    if errors:
        raise ValueError("\n".join(errors))
    if not result:
        raise ValueError("登録する解答用紙を選んでください。")
    return result
