#!/usr/bin/env python3
"""分镜.md에서 미리보기 조립용 샷표 CSV를 만든다(샷 순서·길이·말).

python3 <TOOLS>/pipeline/shotlist.py <project> EP001 [--out 剧集/EP001/制作成果/成片/샷표.csv]

- 샷 = `## SHOT-<EP>-nnn[A-Z]? · 제목` 블록(문서 순서 그대로)
- 길이 = `- 时长：4.1秒` 의 숫자(없으면 3.0)
- 말 = `- 声音：` 줄 안의 VO/旁白/台词/대사/내레이션 뒤 「…」(여럿이면 이어 붙임). 말이 없는 샷은 빈칸
- 쓰임: assemble_preview.py가 --csv 없이 돌면 이 파일을 먼저 찾고, 없으면 이 도구를 불러 만든다
열: 순서, 샷번호(nnn 또는 nnnB — 파일 이름 SHOT-<EP>-<샷번호>와 같다), 샷(제목 앞 코드), 길이초, 대사
"""
from __future__ import annotations

import argparse, csv, re
from pathlib import Path

SPEECH = re.compile(r"(?:VO|V\.O\.|旁白|台词|对白|대사|내레이션|OS)[^「]{0,20}「([^」]+)」")


def rows_from_storyboard(P: Path, ep: str):
    sb = (P / "剧集" / ep / "分镜.md").read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(rf"^## SHOT-{re.escape(ep)}-(\d+[A-Z]?)\s*·?\s*(.*)$", sb, re.M):
        num, title = m.group(1), m.group(2).strip()
        end = sb.find("\n## ", m.end())
        block = sb[m.end(): end if end > 0 else len(sb)]
        d = re.search(r"^- 时长：\s*([\d.]+)", block, re.M)
        snd = re.search(r"^- 声音：(.*)$", block, re.M)
        lines = SPEECH.findall(snd.group(1)) if snd else []
        rows.append({"순서": len(rows) + 1, "샷번호": num, "샷": title.split(" ")[0] if title else num,
                     "길이초": float(d.group(1)) if d else 3.0, "대사": " ".join(x.strip() for x in lines)})
    if not rows:
        raise SystemExit(f"분镜.md에 '## SHOT-{ep}-nnn' 블록이 없다")
    return rows


def write(P: Path, ep: str, out: Path | None = None) -> Path:
    out = out or P / "剧集" / ep / "制作成果" / "成片" / "샷표.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = rows_from_storyboard(P, ep)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["순서", "샷번호", "샷", "길이초", "대사"]); w.writeheader(); w.writerows(rows)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("--out")
    a = ap.parse_args(); P = Path(a.project).resolve()
    out = write(P, a.ep, Path(a.out).resolve() if a.out else None)
    print(out.relative_to(P))


if __name__ == "__main__":
    main()
