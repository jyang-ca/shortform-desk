#!/usr/bin/env python3
"""장소(시퀀스)마다 색·밝기를 맞춘다 — 같은 장소 안 컷의 '변화량'을 줄여 시각 템포를 차분하게(R-CUT-09).

<cv2 파이썬> <TOOLS>/pipeline/grade_match.py <project> <EP> --timeline <미리보기.json> --overrides <preview_overrides.json> [--strength 0.6]

- overrides의 "grade": {"groups": [{"ref": "001", "shots": ["001","002",…]}, …], "strength": 0.6}를 읽고
- 샷마다 쓰는 구간에서 3프레임을 뽑아 RGB 채널 평균·표준편차를 재서, 기준 샷에 맞추는 선형 보정(a·값 + b)을 strength만큼 섞은
  lutrgb 필터를 overrides의 "grade_vf"에 적는다(assemble_preview.py가 vf 뒤에 붙인다).
"""
import argparse, json, re
from pathlib import Path
import cv2, numpy as np


def stats(clip, head, dur):
    cap = cv2.VideoCapture(str(clip)); px = []
    for f in (0.2, 0.5, 0.8):
        cap.set(cv2.CAP_PROP_POS_MSEC, (head + dur * f) * 1000); ok, fr = cap.read()
        if ok:
            px.append(cv2.resize(fr, (180, 320)).reshape(-1, 3).astype(np.float32))
    a = np.concatenate(px)[:, ::-1]  # BGR → RGB
    return a.mean(0), a.std(0) + 1e-3


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("project"); ap.add_argument("ep")
    ap.add_argument("--timeline", required=True); ap.add_argument("--overrides", required=True); ap.add_argument("--strength", type=float)
    a = ap.parse_args(); P = Path(a.project).resolve()
    tl = {s["shot"]: s for s in json.load(open(a.timeline))["shots"]}
    ovp = Path(a.overrides); ov = json.loads(ovp.read_text(encoding="utf-8")); g = ov["grade"]
    s = a.strength if a.strength is not None else g.get("strength", 0.6)
    st = {}
    for n, x in tl.items():
        src = x["source"].split(" (")[0]
        if src.startswith("정지"):
            continue
        m = re.search(r"앞 ([\d.]+)초 자름", x["source"]); head = float(m.group(1)) if m else 0.0
        st[n] = stats(P / src, head, x["duration"])
    out = {}
    for grp in g["groups"]:
        mr, sr = st[grp["ref"]]
        for n in grp["shots"]:
            if n == grp["ref"] or n not in st:
                continue
            ms, ss = st[n]; k = np.clip(sr / ss, 0.9, 1.25)  # 대비를 크게 바꾸지 않는다(v11: 0.6~1.6 → 검정이 뜸)
            A = 1 + s * (k - 1); B = np.minimum(s * (mr - k * ms), 8.0)  # 밝히는 쪽 오프셋은 +8까지(검정 들뜸 방지)
            out[n] = "lutrgb=" + ":".join(f"{c}='clip(val*{A[i]:.3f}+{B[i]:.1f},0,255)'" for i, c in enumerate("rgb"))
    ov["grade_vf"] = out; ov["grade"]["strength"] = s
    ovp.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    print({"graded": len(out), "strength": s})


if __name__ == "__main__":
    main()
