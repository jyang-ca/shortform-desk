#!/usr/bin/env python3
"""컷 문법 자동 검사 — 샷 안 움직임(카메라·피사체)과 이웃 컷 구도 반복.

  <cv2 파이썬> <TOOLS>/qa/cut_grammar.py <project> EP001 --version v08
  (cv2가 있는 파이썬으로 돌린다. preview_check.py가 있으면 알아서 부른다)

입력: 剧集/<EP>/制作成果/成片/<EP>_미리보기_<ver>.mp4 + .json(타임라인)
출력: 成片/_검수/<이름>_cut.json / _cut.md
규칙: 규칙집 R-CAM-01(카메라·피사체 움직임), R-CUT-08(이웃 컷 같은 구도), R-DIR-09(샷 하나 = 작은 동작 하나)
기준선: 같은 장르 AI 쇼츠 12편 632샷 실측(다른 시리즈 제작 때 잼)
  - 카메라가 움직이는 샷 81%(밀기 51%, 배율 변화 중앙 18%), 사진처럼 멈춘 샷 1%, 이웃 컷 같은 구도(상관 > 0.6) 0~3%
"""
import argparse, json, sys
from pathlib import Path

import cv2, numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_motion import analyse  # noqa: E402

BASE = {"moving_cam": 0.81, "still_like": 0.01, "same_setup": 0.01}


def gray(cap, t):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000); ok, f = cap.read()
    return cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (72, 128)).astype(np.float32) if ok else None


def corr(a, b):
    a = (a - a.mean()) / (a.std() + 1e-6); b = (b - b.mean()) / (b.std() + 1e-6)
    return float((a * b).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("--version", required=True)
    a = ap.parse_args()
    P = Path(a.project).resolve(); D = P / "剧集" / a.ep / "制作成果" / "成片"
    name = f"{a.ep}_미리보기_{a.version}"
    mp4 = D / f"{name}.mp4"; tl = json.loads((D / f"{name}.json").read_text(encoding="utf-8"))
    shots = tl["shots"]
    spans = [(s["start"], s["start"] + s["duration"]) for s in shots]
    rows = analyse(str(mp4), spans)
    cap = cv2.VideoCapture(str(mp4))
    same = []
    for p, n in zip(shots, shots[1:]):
        x, y = gray(cap, n["start"] - 0.1), gray(cap, n["start"] + 0.1)
        if x is not None and y is not None:
            c = corr(x, y)
            if c > 0.6:
                same.append({"from": p["shot"], "to": n["shot"], "corr": round(c, 2)})
    for r, s in zip(rows, shots):
        r["shot"] = s["shot"]; r["source"] = s.get("source")
        r["still_like"] = r["cam"] == "static" and r["subject"] < 0.5
    n = len(rows)
    moving = [r["shot"] for r in rows if r["cam"] in ("push", "pull", "pan", "tilt")]
    still = [r["shot"] for r in rows if r["still_like"]]
    R = []

    def add(rule, what, value, v, criterion):
        R.append({"rule": rule, "what": what, "value": value, "verdict": v, "criterion": criterion})

    add("R-CAM-01", "카메라가 움직이는 샷 비율", round(len(moving) / n, 2),
        "pass" if len(moving) / n >= 0.5 else "warn", f"≥ 0.5 (기준선 {BASE['moving_cam']}) — 경고만")
    add("R-CAM-01", "사진처럼 멈춘 샷(카메라 고정 + 피사체 움직임 < 0.5)", still,
        "pass" if len(still) / n <= 0.10 else "warn", f"≤ 10% (기준선 {int(BASE['still_like'] * 100)}%) — 지금 {round(100 * len(still) / n)}%")
    zs = [r["shot"] for r in rows if r["cam"] == "push" and r["subject"] < 0.5]
    add("R-MOV-07", "정지 그림 줌 의심(밀기 + 장면 안 움직임 < 0.5)", zs,
        "pass" if not zs else "warn", "0 — 눈으로 확인: 장면 안에 움직이는 것(안개 흐름·불꽃·사람)이 보이나. 밀기 테이크에 자르기 vf를 겹치면 불합격")
    add("R-CUT-08", "이웃 컷 같은 구도(경계 프레임 상관 > 0.6)", same,
        "pass" if not same else "warn", "0쌍 (디졸브로 잇는 시간 전환 R-CUT-04는 예외) — 안개·어둠은 상관이 부풀므로 눈으로 확인")
    cap.release()
    out = D / "_검수"; out.mkdir(exist_ok=True)
    res = {"preview": str(mp4.relative_to(P)), "baseline": BASE, "checks": R, "shots": rows}
    (out / f"{name}_cut.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    mark = {"pass": "통과", "warn": "경고", "fail": "불합격"}
    md = [f"# {name} 컷 문법 검사", "", f"- 대상: `{res['preview']}` · 기준선: REF-MM 시즌2 632샷", "",
          "| 규칙 | 항목 | 값 | 판정 | 기준 |", "|---|---|---|---|---|"]
    md += [f"| {r['rule']} | {r['what']} | {r['value']} | {mark[r['verdict']]} | {r['criterion']} |" for r in R]
    md += ["", "## 샷별", "", "| 샷 | 길이 | 카메라 | 배율 | 피사체 움직임 | 밝기 | 사진 같음 |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['shot']} | {r['dur']} | {r['cam']} | {r['zoom_total']} | {r['subject']} | {r['luma']} | {'예' if r['still_like'] else ''} |" for r in rows]
    (out / f"{name}_cut.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"checks": [{k: r[k] for k in ("rule", "what", "value", "verdict")} for r in R]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
