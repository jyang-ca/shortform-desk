"""shortform-desk 공용 도움 함수 — 시리즈 설정 읽기, 플러그인 경로, cv2 파이썬 찾기.

도구는 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` 뒤 `import common`으로 쓴다.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PLUGIN_ROOT = TOOLS.parent
TEMPLATES = PLUGIN_ROOT / "templates"
PRODUCE_SCRIPTS = PLUGIN_ROOT / "skills" / "short-drama-produce" / "scripts"
VENV = Path(os.environ.get("SHORTFORM_HOME", Path.home() / ".local" / "share" / "shortform-desk")) / "venv"


def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else v
        return out
    return copy.deepcopy(over)


def load_series(project: Path | str) -> dict:
    """프로젝트의 시리즈/설정.json을 템플릿 기본값 위에 얹어 돌려준다(빠진 키는 기본값)."""
    base = json.loads((TEMPLATES / "시리즈" / "설정.json").read_text(encoding="utf-8"))
    p = Path(project) / "시리즈" / "설정.json"
    if not p.exists():
        raise SystemExit(f"{p} 없음 — 먼저 series-setup 스킬로 시리즈를 설정하세요")
    return _merge(base, json.loads(p.read_text(encoding="utf-8")))


def latest_rulebook(project: Path | str) -> Path | None:
    files = sorted((Path(project) / "검수").glob("규칙집_v*.md"))
    return files[-1] if files else None


def preview_name(series: dict, ep: str, version: str) -> str:
    return f"{ep}_미리보기_{version}"  # 보드·클립 묶음·검사가 모두 이 이름을 찾는다


def cv2_python() -> str | None:
    """cv2(opencv)와 numpy가 있는 파이썬 실행 파일. 순서: SHORTFORM_CV2_PYTHON → 플러그인 venv → 지금 파이썬 → PATH python3."""
    cands = [os.environ.get("SHORTFORM_CV2_PYTHON"), str(VENV / "bin" / "python3"), sys.executable, shutil.which("python3"),
             str(Path.home() / "miniconda3" / "bin" / "python3")]
    for c in cands:
        if c and Path(c).exists():
            r = subprocess.run([c, "-c", "import cv2, numpy"], capture_output=True)
            if r.returncode == 0:
                return c
    return None
