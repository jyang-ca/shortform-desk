#!/usr/bin/env python3
"""미리보기·편집본 자동 검사 (규칙집 편집·소리 영역 중 숫자로 재는 것). 기준 숫자는 시리즈/설정.json format.

python3 <TOOLS>/qa/preview_check.py <project> EP001 --version v03 [--final]

입력: 剧集/<EP>/制作成果/成片/<EP>_미리보기_<ver>.mp4, 같은 이름 .json(타임라인), _narration.wav(내레이션만)
      (assemble_preview.py 출력). --final이면 공개본 기준(정지 샷 불합격 등)으로 더 엄하게 본다.
출력: 成片/_검수/<이름>_qa.json + _qa.md + 오른쪽 아래 모서리 모음 그림(워터마크 확인용)
판정: pass / warn / fail / n/a(미리보기에는 아직 없는 층: 타이포·자막·카드)
표준 라이브러리 + ffmpeg/ffprobe만 쓴다.
"""
import argparse, array, json, math, re, statistics as st, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

SR = 16000


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip()
    return float(out) if out else 0.0


def loudness(path):
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    pk = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    return (float(i.group(1)) if i else None), (float(pk.group(1)) if pk and pk.group(1) != "-inf" else None)


def silences(path, thr, d):
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=n={thr}dB:d={d}", "-f", "null", "-"]).stderr
    s = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    e = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    total = duration(path)
    return [(max(0.0, a), b) for a, b in zip(s, e + [total] * (len(s) - len(e)))], total


def speech_spans(path, thr=-35, d=0.25):
    sil, total = silences(path, thr, d)
    spans, t = [], 0.0
    for a, b in sil:
        if a > t:
            spans.append((t, a))
        t = b
    if t < total:
        spans.append((t, total))
    return spans, sil


def pcm(path):
    raw = run_bytes(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"])
    a = array.array("h"); a.frombytes(raw[: len(raw) // 2 * 2]); return a


def run_bytes(cmd):
    return subprocess.run(cmd, capture_output=True).stdout


def rms_db(a, t0, t1):
    seg = a[int(t0 * SR):int(t1 * SR)]
    if len(seg) < SR * 0.1:
        return None
    m = sum(x * x for x in seg) / len(seg)
    return 10 * math.log10(m / 32768 ** 2) if m > 0 else -100.0


def brightness(path, t0, t1):
    out = run(["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", str(path), "-vf",
               "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"]).stdout
    vals = [float(x) for x in re.findall(r"YAVG=([\d.]+)", out)]
    return st.mean(vals) if vals else None


def verdict(ok, warn=False):
    return "pass" if ok else ("warn" if warn else "fail")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("--version", required=True)
    ap.add_argument("--final", action="store_true", help="공개본 기준")
    a = ap.parse_args()
    P = Path(a.project).resolve(); D = P / "剧集" / a.ep / "制作成果" / "成片"
    S = common.load_series(P); F = S["format"]; LD = F["loudness"]
    name = common.preview_name(S, a.ep, a.version)
    mp4, tl_path, stem = D / f"{name}.mp4", D / f"{name}.json", D / f"{name}_narration.wav"
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    shots = tl["shots"]
    out_dir = D / "_검수"; out_dir.mkdir(exist_ok=True)
    R = []

    def add(rule, what, value, v, criterion):
        R.append({"rule": rule, "what": what, "value": value, "verdict": v, "criterion": criterion})

    total = duration(mp4)
    mx = F["max_seconds"]
    add("R-LEN-01", "전체 길이(초)", round(total, 2), "pass" if total <= mx else "fail", f"{mx}초 이하(설정 format.max_seconds)")
    I, TP = loudness(mp4)
    pl, tol = LD["program_lufs"], LD.get("program_tolerance", 1.0)
    add("R-SFX-05", "통합 음량 LUFS", I, verdict(I is not None and pl - tol <= I <= pl + tol), f"{pl - tol}~{pl + tol}")
    add("R-SFX-05", "최대(true peak) dBTP", TP, verdict(TP is not None and TP <= LD["true_peak_max"]), f"≤ {LD['true_peak_max']}")

    if stem.exists():
        nI, _ = loudness(stem)
        nl = LD["narration_lufs"]
        add("R-NARR-08", "내레이션 줄기 음량 LUFS", nI,
            verdict(nI is not None and nl - 1 <= nI <= nl + 1, warn=nI is not None and nl - 2.3 <= nI <= nl + 2.2), f"{nl - 1}~{nl + 1}")
        spans, sil = speech_spans(stem)
        chars = sum(len(re.sub(r"\s", "", s.get("narration") or "")) for s in shots)  # 문장부호 포함(기준선과 같은 셈)
        if spans:
            speak = sum(s.get("line_duration", s["duration"]) for s in shots if s.get("narration"))  # 말 없는 샷은 빼고(원본 내레이션 파일 기준과 맞춤)
            cps = chars / speak if speak else 0
            lo, hi = F["chars_per_sec"]; top = max(F.get("chars_per_sec_fail_above") or 0, hi + 0.1)
            add("R-LEN-02", "말 빠르기(자/초, 내레이션 샷 길이 합 기준)", round(cps, 2),
                "pass" if lo <= cps <= hi else ("fail" if cps > top else "warn"), f"{lo}~{hi} ({top} 초과 불합격, 범위 밖은 경고)")
            first_sil, _ = silences(stem, -40, 0.05)
            first = first_sil[0][1] if first_sil and first_sil[0][0] <= 0.01 else 0.0
            add("R-OE-01", "첫 말 시작(초)", round(first, 2), verdict(first <= 0.10), "≤ 0.10")
            tail = total - spans[-1][1]
            add("R-OE-04", "마지막 말 끝 → 영상 끝(초)", round(tail, 2), verdict(0.8 <= tail <= 2.0, warn=True), "0.8~2.0")
            long_quiet = [(round(x, 2), round(y - x, 2)) for x, y in sil if y - x > 3.0]
            add("R-LEN-03", "3초 넘는 말 없는 구간", long_quiet, verdict(len(long_quiet) <= 1), "화당 1곳 이하")
            mix = pcm(mp4); speech = [r for r in (rms_db(mix, x, y) for x, y in spans) if r is not None]
            gaps = [r for r in (rms_db(mix, x + 0.05, y - 0.05) for x, y in sil if y - x >= 0.4) if r is not None]
            if speech and gaps:
                g = round(st.median(gaps) - st.median(speech), 1)
                add("R-SFX-01", "효과음 틈 − 말(dB, 중앙값)", g, verdict(-9 <= g <= -4, warn=-11 <= g <= -3), "-9~-4 (목표 -6)")
    else:
        add("R-NARR-08", "내레이션 줄기", None, "n/a", "_narration.wav 없음 — assemble_preview.py로 다시 조립")

    durs = [s["duration"] for s in shots]
    starts = [s["start"] for s in shots]
    f0, f1 = F["first_shot_seconds"]
    add("R-CUT-01", "첫 샷 길이(초)", durs[0], verdict(f0 <= durs[0] <= f1, warn=True), f"{f0}~{f1}")
    cuts3 = sum(1 for t in starts[1:] if t < 3.0)
    add("R-CUT-01", "0~3초 컷 수", cuts3, verdict(cuts3 <= 1), "≤ 1")
    b = brightness(mp4, 0, 3)
    add("R-CUT-01", "0~3초 평균 밝기(Y)", round(b, 1) if b else None, verdict(b is not None and b >= 70, warn=True), "≥ 70")
    mean = st.mean(durs)
    m0, m1 = F["mean_shot_seconds"]; mn = F.get("min_shot_seconds", 1.0)
    add("R-CUT-03", "평균 샷 길이(초)", round(mean, 2), verdict(m0 <= mean <= m1, warn=True), f"{m0}~{m1}")
    short = [s["shot"] for s in shots if s["duration"] < mn]
    add("R-CUT-03", f"{mn}초 미만 샷", short, verdict(not short), "0개")
    last10 = sum(1 for t in starts if t > total - 10)
    add("R-CUT-03", "마지막 10초 컷 수", last10, verdict(last10 <= 2, warn=True), "≤ 2")
    stills = [s["shot"] for s in shots if str(s.get("source", "")).startswith("정지")]
    add("R-CUT-02", "정지 그림 샷", stills, "pass" if not stills else ("fail" if a.final else "warn"),
        "공개본 0개 (미리보기는 표시만)")

    # 컷 문법(R-CAM-01·R-CUT-08) — cv2가 있는 파이썬으로 cut_grammar.py를 부른다
    cg_py = common.cv2_python()
    cg = run([cg_py, str(Path(__file__).resolve().parent / "cut_grammar.py"), str(P), a.ep, "--version", a.version]) if cg_py else None
    cg_json = out_dir / f"{name}_cut.json"
    if cg is not None and cg.returncode == 0 and cg_json.exists():
        for r in json.loads(cg_json.read_text(encoding="utf-8"))["checks"]:
            add(r["rule"], r["what"], r["value"], r["verdict"], r["criterion"])
    else:
        add("R-CAM-01", "컷 문법 검사", None, "n/a", "cv2 파이썬 없음 또는 실패 — doctor로 설치 확인 후 qa/cut_grammar.py를 따로 돌린다")

    # 손질 겹침(R-MOV-07): 밀기 테이크 위 자르기 vf = 정지 그림 줌, 새 테이크·그림에 남은 옛 자르기
    ovp = next((q for q in (tl_path.parent / f"preview_overrides_{a.version}.json", tl_path.parent / "preview_overrides.json") if q.exists()), None)
    if ovp:
        ov = json.loads(ovp.read_text(encoding="utf-8")); vf = ov.get("vf", {})
        cgr = {r["shot"]: r for r in json.loads(cg_json.read_text(encoding="utf-8"))["shots"]} if cg_json.exists() else {}
        stack = [s["shot"] for s in shots if "crop" in vf.get(s["shot"], "") and ("_push/" in str(s.get("source", "")) or cgr.get(s["shot"], {}).get("cam") == "push")
                 and cgr.get(s["shot"], {}).get("subject", 0) < 1.0]  # 장면 안 움직임이 있는 따라가기는 제외
        add("R-MOV-07", f"밀기 위 자르기 겹침({ovp.name})", stack, "fail" if stack else "pass", "0 — 밀기 테이크에 vf crop을 겹치지 않는다(정지 그림 줌처럼 보임)")
        stale = [s["shot"] for s in shots if "crop" in vf.get(s["shot"], "")]
        add("R-MOV-07", "자르기 vf가 걸린 샷(테이크·그림을 바꿨으면 지울 것)", stale, "pass" if not stale else "warn", "눈으로 확인 — 테이크를 바꾼 샷에 옛 자르기가 남지 않았나")

    # 워터마크 확인용 모서리 모음(샷마다 가운데 한 장)
    sheet = out_dir / f"{name}_모서리.png"
    sel = "+".join(f"between(t,{s['start'] + s['duration'] / 2:.2f},{s['start'] + s['duration'] / 2 + 0.05:.2f})" for s in shots)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf",
         f"select='{sel}',crop=iw*0.16:ih*0.12:iw*0.84:ih*0.86,scale=160:-1,tile=8x{math.ceil(len(shots) / 8)}",
         "-frames:v", "1", "-vsync", "vfr", str(sheet)])
    add("R-CARD-03", "오른쪽 아래 모서리 모음(눈으로 확인)", str(sheet.relative_to(P)), "n/a", "워터마크(✦ 등) 없음 — 검수 에이전트가 본다")
    for rule, what in (("R-TYPO-01~05", "여는 타이포"), ("R-SUB-01~05", "자막"), ("R-CARD-01~02", "끝 카드")):
        add(rule, what, None, "n/a", "미리보기에는 아직 없음 — 편집본에서 검사")

    summary = {k: sum(1 for r in R if r["verdict"] == k) for k in ("pass", "warn", "fail", "n/a")}
    res = {"preview": str(mp4.relative_to(P)), "final_mode": a.final, "summary": summary, "checks": R}
    (out_dir / f"{name}_qa.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    mark = {"pass": "통과", "warn": "경고", "fail": "불합격", "n/a": "-"}
    md = [f"# {name} 자동 검사", "", f"- 대상: `{res['preview']}` · 기준: {'공개본' if a.final else '미리보기'}",
          f"- 통과 {summary['pass']} · 경고 {summary['warn']} · 불합격 {summary['fail']}", "",
          "| 규칙 | 항목 | 값 | 판정 | 기준 |", "|---|---|---|---|---|"]
    md += [f"| {r['rule']} | {r['what']} | {r['value']} | {mark[r['verdict']]} | {r['criterion']} |" for r in R]
    (out_dir / f"{name}_qa.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "fail": [f"{r['rule']} {r['what']}={r['value']}" for r in R if r["verdict"] == "fail"],
                      "warn": [f"{r['rule']} {r['what']}={r['value']}" for r in R if r["verdict"] == "warn"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
