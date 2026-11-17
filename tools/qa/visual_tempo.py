#!/usr/bin/env python3
"""시각 템포 재기 — 샷 길이만이 아니라 '컷마다 화면이 얼마나 바뀌나'까지(R-CUT-09).

<cv2 파이썬> <TOOLS>/qa/visual_tempo.py <video> --cuts <measure.json | 미리보기 timeline.json> [--label X] [--json out.json]

- 샷 길이: 평균·중앙·1.5초 미만 비율·1.2초 미만 비율
- 컷 변화량(cut_change): 컷 앞뒤 0.1초 프레임의 (1 - 회색 72×128 상관)/4 + HSV 색 히스토그램 바타차리야 거리/2 → 0(같음)~1(전혀 다름)
- 10초당 변화량(change_per_10s): 컷 변화량 합 ÷ 길이 × 10 — 컷이 잦을수록, 컷마다 크게 바뀔수록 크다(체감 빠르기)
- 큰 변화 컷(cut_change > 0.5) 비율
"""
import argparse, json, statistics as st
import cv2, numpy as np


def frame(cap, t):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000); ok, f = cap.read()
    return f if ok else None



def measure(video, cutsf, label=None):
    d = json.load(open(cutsf)); cap = cv2.VideoCapture(video)
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
    cuts = d["cuts"] if "cuts" in d else [s["start"] for s in d["shots"][1:]]
    edges = [0.0] + list(cuts) + [dur]; sl = [b - a for a, b in zip(edges, edges[1:]) if b - a > 0.04]
    ch = []
    for t in cuts:
        a, b = frame(cap, t - 0.1), frame(cap, t + 0.1)
        if a is None or b is None:
            continue
        ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (72, 128)).astype(np.float32)
        gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (72, 128)).astype(np.float32)
        ga = (ga - ga.mean()) / (ga.std() + 1e-6); gb = (gb - gb.mean()) / (gb.std() + 1e-6)
        corr = float((ga * gb).mean())
        ha = cv2.calcHist([cv2.cvtColor(a, cv2.COLOR_BGR2HSV)], [0, 1, 2], None, [12, 8, 8], [0, 180, 0, 256, 0, 256])
        hb = cv2.calcHist([cv2.cvtColor(b, cv2.COLOR_BGR2HSV)], [0, 1, 2], None, [12, 8, 8], [0, 180, 0, 256, 0, 256])
        cv2.normalize(ha, ha); cv2.normalize(hb, hb)
        bh = float(cv2.compareHist(ha, hb, cv2.HISTCMP_BHATTACHARYYA))
        ch.append(0.25 * (1 - max(-1.0, min(1.0, corr))) + 0.5 * bh)  # 구도 (1-상관)/4 + 색 거리/2 → 0~1
    return {"label": label or video, "dur": round(dur, 1), "shots": len(sl), "asl": round(st.mean(sl), 2), "median": round(st.median(sl), 2),
            "under1_5": round(sum(x < 1.5 for x in sl) / len(sl), 2), "under1_2": round(sum(x < 1.2 for x in sl) / len(sl), 2),
            "cut_change_mean": round(st.mean(ch), 3) if ch else None, "big_change_share": round(sum(x > 0.5 for x in ch) / len(ch), 2) if ch else None,
            "change_per_10s": round(sum(ch) / dur * 10, 2) if ch else None}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--cuts", required=True); ap.add_argument("--label"); ap.add_argument("--json")
    a = ap.parse_args(); r = measure(a.video, a.cuts, a.label)
    if a.json:
        json.dump(r, open(a.json, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
