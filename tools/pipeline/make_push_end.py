#!/usr/bin/env python3
"""카메라 밀기(R-CAM-01) 끝 프레임 만들기: 시작 프레임의 가운데를 k(기본 0.88)만큼 잘라 원래 크기로 키운다.

<cv2 파이썬> <TOOLS>/pipeline/make_push_end.py <project> <EP> [--k 0.88] [--center 0.5,0.5] [--only 003,002B]

视频提示词.md의 MOTION 항목 중 REF-END가 `images/_push/…_push88.png`인 것을 찾아, 같은 항목의 REF-START 그림으로 만든다.
(시작 프레임을 편집해 만든 끝 프레임 — CLAUDE.md 일관성 규칙 1. 새로 생성하지 않는다)
"""
import argparse, re
from pathlib import Path
from PIL import Image


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("project"); ap.add_argument("ep")
    ap.add_argument("--k", type=float, default=0.88); ap.add_argument("--center", default="0.5,0.5"); ap.add_argument("--only", default="")
    a = ap.parse_args()
    P = Path(a.project).resolve(); vd = (P / "剧集" / a.ep / "视频提示词.md").read_text(encoding="utf-8")
    cx, cy = (float(v) for v in a.center.split(","))
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    made = []
    for m in re.finditer(r"^- 输入参考图：REF-START（顺序：1）· (\S+?)《[^\n]*?REF-END（顺序：2）· (\S+?_push88\.png)《", vd, re.M):
        start, end = P / m.group(1), P / m.group(2)
        n = re.search(r"SHOT-EP\d{3}-(\w+?)_push88", end.name).group(1)
        if only and n not in only:
            continue
        if not start.exists():
            print("시작 프레임 없음:", m.group(1)); continue
        im = Image.open(start).convert("RGB"); w, h = im.size
        cw, ch = round(w * a.k), round(h * a.k)
        x0 = min(max(0, round(cx * w - cw / 2)), w - cw); y0 = min(max(0, round(cy * h - ch / 2)), h - ch)
        end.parent.mkdir(parents=True, exist_ok=True)
        im.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), Image.LANCZOS).save(end)
        made.append(n)
    print({"made": made})


if __name__ == "__main__":
    main()
