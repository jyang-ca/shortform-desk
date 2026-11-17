#!/usr/bin/env python3
"""회차 미리보기 영상 조립: 샷 순서대로 영상 테이크를 잇고 내레이션을 깐다(보기용 드래프트, 최종 편집 아님).

python3 <TOOLS>/pipeline/assemble_preview.py <project> <EP> [--version v01] [--tempo 1.25] [--csv 샷표.csv]

- 샷 순서·말·말 없는 샷 길이: --csv, 없으면 成片/샷표.csv, 그것도 없으면 分镜.md에서 만든다(shotlist.py)
- 테이크: 剧集/<EP>/制作成果/video/MOTION-<EP>-nnn_tNN.mp4 중 _검수.json 판정이 '다시'가 아닌 가장 높은 번호
- 영상이 없는 샷: 分镜.md의 起始帧(REF-START) 그림을 정지 화면으로
- 내레이션(剧集/<EP>/制作成果/tts/SHOT-<EP>-nnn_vo01.mp3) 처리 — 규칙집 2부:
  앞 무음 자르기 -50dB(R-NARR-04: -35dB는 끝 음절을 자름), 줄 안 쉼 상한 0.25초(R-LEN-03),
  끝 무음은 0.08초만 남김, 템포 --tempo(최대 1.25, R-LEN-02)
- 말과 영상을 따로 놓는다: 말은 줄마다 이어 놓고(줄 뒤 여백: 문장 끝 0.08초·쉼표 끝 0.03초), 컷 자리는 말과 따로 옮길 수 있다.
- 회차별 손질(있으면): 剧集/<EP>/制作成果/成片/preview_overrides.json
  {"shot_dur": {"022": 1.0},                                       말 없는 샷 길이
   "cut": {"002": {"abs": 2.5}, "013": {"line_offset": 0.42}},     그 샷 영상이 시작하는 자리(절대 초 / 그 샷 말 시작 + 초)
   "trim_head": {"MOTION-EP001-017_t03.mp4": 0.4},                 테이크 앞 자르기
   "vo_fade_out": {"026": 0.05},                                   줄 끝 페이드(잡음)
   "vf": {"007": "eq=gamma=1.2"},                                  샷별 화면 보정
   "split": {"002": [{"shot": "002B", "line_offset": 1.5}]},       긴 줄 나누기(R-DIR-10): 부모 말 시작 + 초에서 새 샷으로 컷
   "drop": {"012": "next"},  "cut": {"023": {"shift": -0.5}},     샷 그림 빼기(말은 그대로)·컷 옮기기(R-CUT-09)
   "take": {"001": "剧集/…/video/_push/MOTION-…_p01.mp4"},          판별 테이크 지정(--overrides로 다른 판)
   "end_hold": 1.0, "pad_sentence": 0.08, "pad_comma": 0.03}
- 음량: 시리즈/설정.json format.loudness(기본 내레이션 줄기 -18.2 LUFS, 전체 -15 LUFS), 최대 -1.5 dBTP(두 번 재는 선형 loudnorm)
- 화면 크기·fps: format.preview_size·fps(기본 720×1280, 24)
- 출력: 剧集/<EP>/制作成果/成片/<EP>_미리보기_<version>.mp4 + .json(타임라인) + _narration.wav(내레이션만, 검사용)
"""
import argparse, csv, json, re, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from pipeline import shotlist  # noqa: E402

W, H, FPS = 720, 1280, 24
AMB_DB = -16.0     # (v10: -20 → -16. 조용한 클립은 게인 상한 +21dB에 걸려 이 값이 듣지 않음 — v10 틈 -8.4dB) 클립 소리 평균(말 틈에서 말보다 약 6dB 아래 — R-SFX-01 목표 -6)
DUCK = 0.5         # 말하는 동안 클립 소리 -6dB


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    return bool(out)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def mean_db(path, start=0.0, dur=None):
    args = ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-i", str(path)] + (["-t", str(dur)] if dur else []) + \
           ["-af", "volumedetect", "-f", "null", "-"]
    m = re.search(r"mean_volume: (-?[\d.]+) dB", subprocess.run(args, capture_output=True, text=True).stderr)
    return float(m.group(1)) if m else None


def loudnorm2(src_args, filt_prefix, target, tp, out_args):
    """두 번 재는 선형 loudnorm(한 번만 재면 조용한 곳을 끌어올려 효과음·쉼 크기가 바뀐다)."""
    first = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *src_args, "-filter_complex",
                            f"{filt_prefix}loudnorm=I={target}:TP={tp}:LRA=20:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(first[first.rfind("{"):first.rfind("}") + 1])
    ln = (f"loudnorm=I={target}:TP={tp}:LRA=20:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    ff(*src_args, "-filter_complex", f"{filt_prefix}{ln},aresample=176400,alimiter=limit=0.7:attack=1:release=50:level=false,aresample=44100[a]", *out_args)  # 4배 오버샘플링 리미터(압축 뒤 true peak 대비)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep")
    ap.add_argument("--csv", help="샷표 CSV(프로젝트 기준 경로). 없으면 成片/샷표.csv → 分镜.md")
    ap.add_argument("--tempo", type=float); ap.add_argument("--version", default="v01")
    ap.add_argument("--overrides", help="다른 손질 파일(예: 카메라 밀기 판) — 기본 成片/preview_overrides.json")
    a = ap.parse_args()
    global W, H, FPS
    P = Path(a.project).resolve(); EPD = P / "剧集" / a.ep; MEDIA = EPD / "制作成果"
    S = common.load_series(P); F = S["format"]; LD = F["loudness"]
    W, H = F.get("preview_size", [W, H]); FPS = F.get("fps", FPS)
    tmax = F.get("tts_tempo_max", 1.25)
    a.tempo = tmax if a.tempo is None else a.tempo
    if a.tempo > tmax:
        raise SystemExit(f"템포는 {tmax}배까지(R-LEN-02, 설정 format.tts_tempo_max) — 길이는 대본으로 줄인다")
    csv_path = P / a.csv if a.csv else MEDIA / "成片" / "샷표.csv"
    if not csv_path.exists():
        csv_path = shotlist.write(P, a.ep)
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    pname = common.preview_name(S, a.ep, a.version)
    verdicts = json.loads((MEDIA / "_검수.json").read_text(encoding="utf-8")) if (MEDIA / "_검수.json").exists() else {}
    storyboard = (EPD / "分镜.md").read_text(encoding="utf-8")
    out_dir = MEDIA / "成片"; out_dir.mkdir(parents=True, exist_ok=True)
    ov_path = Path(a.overrides).resolve() if a.overrides else out_dir / "preview_overrides.json"
    ov = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    pad_s, pad_c, end_hold = ov.get("pad_sentence", 0.08), ov.get("pad_comma", 0.03), ov.get("end_hold", 1.0)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1) 말 타임라인
        shots, t = [], 0.0
        for r in rows:
            n = (r.get("샷번호") or "").strip() or f"{int(r['순서']):03d}"
            vo = MEDIA / f"tts/SHOT-{a.ep}-{n}_vo01.mp3"
            s = {"shot": n, "code": r["샷"], "start": round(t, 3)}
            if vo.exists():
                proc = tmp / f"{n}_vo.wav"
                chain = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03:"
                         "stop_periods=-1:stop_duration=0.25:stop_threshold=-50dB:stop_silence=0.25,"
                         "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse,"
                         f"atempo={a.tempo}")
                ff("-i", str(vo), "-af", chain, "-ar", "44100", "-ac", "2", str(proc))
                d = probe(proc)
                if n in ov.get("vo_fade_out", {}):
                    fd = ov["vo_fade_out"][n]; faded = tmp / f"{n}_vof.wav"
                    ff("-i", str(proc), "-af", f"afade=t=out:st={max(0, d - fd):.3f}:d={fd}", str(faded)); proc = faded
                text = (r.get("대사") or "").strip()
                pad = pad_c if text.endswith((",", "，", "、")) else pad_s
                lead = min(0.03, pad)  # 컷 뒤 첫소리까지(약 1프레임) — 줄 사이 여백 안에서
                s.update(vo=str(proc), vo_start=round(t + lead, 3), vo_dur=round(d, 3), narration=text)
                s["dur"] = round(d + pad, 3)
            else:
                s.update(narration=None)
                s["dur"] = float(ov.get("shot_dur", {}).get(n, r["길이초"]))
            shots.append(s); t += s["dur"]
        # 1-b) 긴 줄 나누기(R-DIR-10): 부모 줄 말 시작 + line_offset 자리에서 새 샷으로 컷. 말은 부모 줄 그대로.
        for par, items in ov.get("split", {}).items():
            i = next(k for k, x in enumerate(shots) if x["shot"] == par)
            p = shots[i]; end = p["start"] + p["dur"]; base = p.get("vo_start", p["start"]); p["line_full"] = p["dur"]
            new = [{"shot": it["shot"], "code": it.get("code", p["code"] + "b"), "start": round(base + it["line_offset"], 3),
                    "narration": None, "line_of": par} for it in items]
            seq = [p] + new
            for x0, x1 in zip(seq, seq[1:]):
                x0["dur"] = round(x1["start"] - x0["start"], 3)
            new[-1]["dur"] = round(end - new[-1]["start"], 3)
            shots[i + 1:i + 1] = new
        speech_end = max((x["vo_start"] + x["vo_dur"] for x in shots if x.get("vo")), default=None)
        total = max(t, speech_end + end_hold) if speech_end is not None else t
        shots[-1]["dur"] = round(total - shots[-1]["start"], 3)

        # 2) 컷 자리(영상 시작) — 말과 따로 옮긴다
        vstart = [x["start"] for x in shots]
        for i, x in enumerate(shots):
            c = ov.get("cut", {}).get(x["shot"])
            if c and i > 0:
                if "shift" in c:
                    vstart[i] += c["shift"]
                else:
                    vstart[i] = c["abs"] if "abs" in c else x.get("vo_start", x["start"]) + c["line_offset"]
        # 샷 빼기(R-CUT-09): 그림만 뺀다 — 말은 그대로. next = 다음 샷이 그 자리부터, prev = 앞 샷이 이어서
        drop = ov.get("drop", {})
        for i, x in enumerate(shots):
            if x["shot"] in drop:
                x["dropped"] = drop[x["shot"]]
                if drop[x["shot"]] == "next" and i + 1 < len(shots):
                    vstart[i + 1] = vstart[i]
                else:
                    vstart[i] = vstart[i + 1] if i + 1 < len(shots) else total
        vstart.append(total)
        frames = [round(v * FPS) for v in vstart]  # 컷은 프레임 격자 위에(조각을 초로 자르면 뒤로 갈수록 밀린다 — R-CUT-06)
        total = frames[-1] / FPS
        for i, x in enumerate(shots):
            x["frames"] = frames[i + 1] - frames[i]
            x["video_start"], x["video_dur"] = round(frames[i] / FPS, 4), round(x["frames"] / FPS, 4)
            if x["frames"] <= 0 and not x.get("dropped"):
                raise SystemExit(f"컷 자리가 겹친다: {x['shot']}")

        # 3) 영상 조각(+ 클립 소리)
        segs = []
        for x in shots:
            n = x["shot"]
            if x.get("dropped"):
                continue
            takes = sorted(MEDIA.glob(f"video/MOTION-{a.ep}-{n}_t*.mp4"))
            takes = [p for p in takes if verdicts.get(str(p.relative_to(P)), {}).get("verdict") != "다시"]
            clip = takes[-1] if takes else None
            if n in ov.get("take", {}):  # 판별 테이크 지정(예: 카메라 밀기 판)
                clip = P / ov["take"][n]
            dur = x["video_dur"]; seg = tmp / f"{n}.mp4"; ambw = tmp / f"{n}_amb.wav"
            asr = f"apad,atrim=0:{dur:.4f},asetpts=PTS-STARTPTS"
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
            if n in ov.get("vf", {}):  # 샷별 보정(예: 이웃 샷 밝기 맞춤 R-CUT-07)
                vf += "," + ov["vf"][n]
            if n in ov.get("grade_vf", {}):  # 장소별 색 맞춤(grade_match.py, R-CUT-09)
                vf += "," + ov["grade_vf"][n]
            if clip:
                head = ov.get("trim_head", {}).get(clip.name, 0.0)
                avail = probe(clip) - head
                slow = dur / avail if avail > 0 else 1
                speed = f"setpts=PTS*{slow:.4f}," if 1.0 < slow <= 1.12 else ""  # 조금 모자라면 늦춤, 많이 모자라면 끝 정지
                vchain = f"[0:v]{speed}{vf},fps={FPS},tpad=stop_mode=clone:stop_duration={dur}[v]"
                # 클립 소리 = 효과음 층(R-SFX-01): 쓰는 구간을 평균 AMB_DB로 맞춤(게인 -15~+21dB), 딸깍 같은 순간 소리는 -9dBFS로 눌러 둔다(게인으로 커진 클릭이 전체 최대치를 넘김 — v09 009·040)
                lvl = mean_db(clip, head, dur) if has_audio(clip) else None
                gain = max(-15.0, min(21.0, AMB_DB - lvl)) if lvl is not None and lvl > -80 else None
                ff("-ss", str(head), "-i", str(clip), "-filter_complex", vchain, "-map", "[v]", "-frames:v", str(x["frames"]),
                   "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(seg))
                if gain is not None:
                    tempo = f"atempo={1 / slow:.4f}," if speed else ""  # 그림을 늦췄으면 소리도 같은 비율로
                    ff("-ss", str(head), "-i", str(clip), "-af", f"{tempo}volume={gain:.1f}dB,aresample=176400,alimiter=limit=0.45:attack=0.5:release=40:level=false,aresample=44100,{asr}", "-ac", "2", "-c:a", "pcm_s16le", str(ambw))
                else:
                    ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-af", asr, "-c:a", "pcm_s16le", str(ambw))
                x["source"] = str(clip.relative_to(P)) + (f" (앞 {head}초 자름)" if head else "") + (f" (×{slow:.2f} 늦춤)" if speed else "")
            else:
                m = re.search(rf"## SHOT-{a.ep}-{n}\b[\s\S]*?REF-START（顺序：\d+）· ([^《]+)《", storyboard)
                if not m:  # 시작 프레임을 아직 안 골랐으면 후보 중 가장 높은 번호
                    cands = sorted(MEDIA.glob(f"images/SHOT-{a.ep}-{n}_c*.png"))
                    if not cands:
                        raise SystemExit(f"{n}: 영상도 키프레임도 없다 — 分镜.md REF-START 또는 images/SHOT-{a.ep}-{n}_c01.png")
                    still = cands[-1]
                else:
                    still = P / m.group(1).strip()
                ff("-loop", "1", "-i", str(still), "-vf", f"{vf},fps={FPS}", "-frames:v", str(x["frames"]), "-an",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(seg))
                ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-af", asr, "-c:a", "pcm_s16le", str(ambw))
                x["source"] = "정지 화면 " + str(still.relative_to(P))
            segs.append((seg, ambw))
        lst = tmp / "list.txt"; lst.write_text("".join(f"file '{v}'\n" for v, _ in segs), encoding="utf-8")
        picture = tmp / "picture.mp4"
        ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(picture))
        alst = tmp / "alist.txt"; alst.write_text("".join(f"file '{w}'\n" for _, w in segs), encoding="utf-8")
        ambience = tmp / "ambience.wav"  # 클립 소리는 WAV로 이어 붙인다(AAC 조각을 이으면 소리가 점점 늦어진다)
        ff("-f", "concat", "-safe", "0", "-i", str(alst), "-c", "copy", str(ambience))

        # 4) 내레이션 줄기(이어 놓기) → -18.2 LUFS(R-NARR-08)
        vos = [x for x in shots if x.get("vo")]
        stem = out_dir / f"{pname}_narration.wav"
        if not vos:  # 내레이션 없는 회차: 조용한 줄기(검사는 n/a로)
            ff("-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo", "-t", str(total), str(stem))
            talk = "0"
        inputs = sum((["-i", x["vo"]] for x in vos), [])
        delays = ";".join(f"[{i}:a]adelay={int(x['vo_start'] * 1000)}|{int(x['vo_start'] * 1000)}[d{i}]" for i, x in enumerate(vos))
        mix = "".join(f"[d{i}]" for i in range(len(vos))) + f"amix=inputs={len(vos)}:normalize=0,apad=whole_dur={total}"
        if vos:
            stem_raw = tmp / "stem_raw.wav"
            ff(*inputs, "-filter_complex", f"{delays};{mix}[o]", "-map", "[o]", "-t", str(total), "-ar", "44100", "-ac", "2", str(stem_raw))
            loudnorm2(["-i", str(stem_raw)], "[0:a]", LD.get("narration_lufs", -18.2), -2, ["-map", "[a]", "-ar", "44100", str(stem)])

        # 5) 합치기 → -16 LUFS, 최대 -1.5 dBTP
        final = out_dir / f"{pname}.mp4"
        talk = "+".join(f"between(t,{x['vo_start'] - 0.08:.2f},{x['vo_start'] + x['vo_dur'] + 0.25:.2f})" for x in vos) or "0"
        duck = f"[0:a]volume='if({talk},{DUCK},1)':eval=frame[amb];"
        loudnorm2(["-i", str(ambience), "-i", str(stem)], f"{duck}[amb][1:a]amix=inputs=2:normalize=0,", LD.get("program_lufs", -15.0), -2.5,
                  ["-i", str(picture), "-map", "2:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                   "-movflags", "+faststart", str(final)])

    # 6) 압축(AAC) 뒤 최대치가 -1 dBTP를 넘으면 그만큼 내려 다시 쓴다(R-SFX-05)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(final), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    pk = re.search(r"Peak:\s+(-?[\d.]+) dBFS", err[err.rfind("Summary:"):])
    if pk and float(pk.group(1)) > -1.2:
        fixed = final.with_name(final.stem + "_tp.mp4")
        ff("-i", str(final), "-c:v", "copy", "-af", f"volume={-1.5 - float(pk.group(1)):.2f}dB", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(fixed))
        fixed.replace(final)

    # 뺀 샷의 말은 그림을 이어받은 샷에 붙여 적는다
    for i, x in enumerate(shots):
        if x.get("dropped"):
            j = i + 1 if x["dropped"] == "next" and i + 1 < len(shots) else i - 1
            y = shots[j]
            if x.get("narration"):
                y["narration"] = (x["narration"] + " " + (y.get("narration") or "")).strip() if j > i else ((y.get("narration") or "") + " " + x["narration"]).strip()
            if y.get("line_of") == x["shot"]:   # 나눈 줄의 부모를 뺐다 → 자식이 그 줄 전체를 맡음
                y["line_full"] = x.get("line_full", x["dur"])
            else:
                y["line_full"] = y.get("line_full", y["dur"]) + x.get("line_full", x["dur"])
            if j > i:
                y["start"] = x["start"]
    shots = [x for x in shots if not x.get("dropped")]
    timeline = [{"shot": x["shot"], "code": x["code"], "start": round(x["video_start"], 2), "duration": round(x["video_dur"], 3),
                 "line_start": x["start"], "line_duration": x.get("line_full", x["dur"]), "vo_start": x.get("vo_start"), "vo_dur": x.get("vo_dur"),
                 "source": x["source"], "narration": x["narration"], **({"line_of": x["line_of"]} if x.get("line_of") else {})} for x in shots]
    (out_dir / f"{pname}.json").write_text(
        json.dumps({"tempo": a.tempo, "total_seconds": round(total, 2), "overrides": ov, "shots": timeline},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"output": str(final.relative_to(P)), "seconds": round(total, 2), "shots": len(timeline),
                      "stills": [x["shot"] for x in timeline if x["source"].startswith("정지")]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
