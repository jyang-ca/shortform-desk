#!/usr/bin/env python3
"""샷 안 카메라 움직임·피사체 움직임·밝기를 잰다(대조용 공통 자).

cv2가 있는 파이썬으로 돌린다(doctor가 만든 venv):
  <cv2 파이썬> <TOOLS>/qa/shot_motion.py <video> --cuts <measure.json | timeline.json> --out <x.motion.json>

- cuts: drama-skills breakdown measure.json 의 cuts, 또는 assemble_preview 타임라인(shots[].start/duration)
- 샷마다 약 8fps로 프레임을 뽑아 이웃 프레임 사이 특징점 흐름(LK)에서 아핀(이동·배율)을 추정
  - cam: static | push(배율↑) | pull(배율↓) | pan(좌우) | tilt(상하) | handheld(방향 없이 흔들림)
  - zoom_total: 샷 전체 배율 변화(1.06 = 6% 밀기), pan_total: 화면 폭 대비 이동
  - subject: 전역 움직임을 뺀 남은 흐름의 상위 10% 크기(화면 대각선 대비 %/초) — 인물·소품이 얼마나 움직이나
  - luma: 평균 밝기(0~255), luma_drift: 샷 안 밝기 변화
"""
import argparse, json, math
import cv2, numpy as np


def load_cuts(path, duration):
    d = json.load(open(path))
    if "cuts" in d:
        cuts = [c if isinstance(c, (int, float)) else c.get("t", c.get("time")) for c in d["cuts"]]
        edges = [0.0] + sorted(float(c) for c in cuts) + [duration]
    else:
        edges = [0.0] + [float(s["start"]) for s in d["shots"][1:]] + [duration]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] - edges[i] > 0.05]


def analyse(video, spans, step=1 / 8):
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS); W = cap.get(cv2.CAP_PROP_FRAME_WIDTH); H = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    diag = math.hypot(W, H); out = []
    for i, (a, b) in enumerate(spans):
        times = np.arange(a + 0.04, b - 0.04, step)
        if len(times) < 2:
            times = np.array([a + (b - a) / 2])
        frames = []
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, f = cap.read()
            if ok:
                frames.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (360, 640)))
        luma = [float(f.mean()) for f in frames]
        zoom = 1.0; tx = ty = 0.0; jitter = []; subj = []
        for f0, f1 in zip(frames, frames[1:]):
            p0 = cv2.goodFeaturesToTrack(f0, 300, 0.01, 8)
            if p0 is None or len(p0) < 12:
                continue
            p1, st, _ = cv2.calcOpticalFlowPyrLK(f0, f1, p0, None)
            g = st.ravel() == 1
            if g.sum() < 12:
                continue
            A, inl = cv2.estimateAffinePartial2D(p0[g], p1[g], ransacReprojThreshold=2.0)
            if A is None:
                continue
            s = math.hypot(A[0, 0], A[1, 0]); zoom *= s; tx += A[0, 2]; ty += A[1, 2]
            jitter.append((A[0, 2], A[1, 2]))
            pred = cv2.transform(p0[g], A); res = np.linalg.norm((p1[g] - pred).reshape(-1, 2), axis=1)
            subj.append(float(np.percentile(res, 90)))
        dur = b - a
        pan = tx / 360; tilt = ty / 640
        jit = float(np.std([j[0] for j in jitter]) + np.std([j[1] for j in jitter])) if len(jitter) > 2 else 0.0
        if abs(zoom - 1) >= 0.03 and abs(zoom - 1) * 3 > max(abs(pan), abs(tilt)):
            cam = "push" if zoom > 1 else "pull"
        elif abs(pan) >= 0.04 and abs(pan) >= abs(tilt):
            cam = "pan"
        elif abs(tilt) >= 0.04:
            cam = "tilt"
        elif jit > 1.2:
            cam = "handheld"
        else:
            cam = "static"
        subject = (np.mean(subj) / (diag * 360 / W) * 100 / step) if subj else 0.0
        out.append({"i": i + 1, "start": round(a, 3), "end": round(b, 3), "dur": round(dur, 3), "cam": cam,
                    "zoom_total": round(zoom, 3), "pan_total": round(pan, 3), "tilt_total": round(tilt, 3),
                    "jitter": round(jit, 2), "subject": round(float(subject), 2),
                    "luma": round(float(np.mean(luma)), 1) if luma else None,
                    "luma_drift": round(float(luma[-1] - luma[0]), 1) if len(luma) > 1 else 0.0})
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--cuts", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.video); dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS); cap.release()
    shots = analyse(a.video, load_cuts(a.cuts, dur))
    json.dump({"video": a.video, "duration": round(dur, 3), "shots": shots}, open(a.out, "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(a.out, len(shots), dict(Counter(s["cam"] for s in shots)))


if __name__ == "__main__":
    main()
