#!/usr/bin/env python3
"""촬영 지시서(dp 에이전트가 쓴 JSON)를 영상 문장으로 조립해 文档에 넣는다.

python3 <TOOLS>/pipeline/dp_apply.py <project> <EP> <지시서.json> [--only 014,015] [--dry]

- 카메라 6칸·지킴 줄·소리는 장부 `검수/카메라문장_장부.json`의 id로 고른다(지시서에 영어를 직접 쓰면 `free` 칸 — 장부에 없는 시험 말).
- status 'bad' id를 쓰면 멈춘다. 'test' id는 쓰되 결과에 표시한다.
- 고치는 곳: 视频提示词.md 해당 MOTION 항목(时长·输入参考图·起始帧·状态链·终点·문장), 分镜.md 해당 SHOT 항목(时长·景别·机位·运镜·시점·终点·输入参考图).
- gen = start(시작 프레임만) | hold(시작=끝, hold_last_frame) | start_end(끝 = 시작 가운데 88%, images/_push/에 만듦).
- 인물 문장: 샷의 "characters"(예: ["C1"], 없으면 설정의 첫 인물)마다 시리즈/설정.json characters[].video_sentence, "hands": true면 hands_sentence.
문장 순서(Gemini Omni 가이드): 시작 프레임 → 화면 크기·높이 → 동작 → 카메라 관계·움직임 → 렌즈 → 끝·금지 → 지킴 → 소리.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

HEAD = ("Animate this start frame as one continuous shot with no scene cuts. Keep the lighting, colors and every object exactly as in the start frame.")
TAIL = "No dialogue, no voice, no music. No on-screen text, no subtitles, no logos."
SLOTS = ["1_size", "2_height", "3_relation", "4_move", "5_lens", "6_end"]


def sent(s):
    s = s.strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?;:)" else s + "."


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("spec")
    ap.add_argument("--only", default=""); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(); P = Path(a.project).resolve(); D = P / "剧集" / a.ep
    chars = {c["id"]: c for c in common.load_series(P).get("characters", []) if c.get("id")}
    first = next(iter(chars), None)
    led = {x["id"]: x for x in json.loads((P / "검수" / "카메라문장_장부.json").read_text(encoding="utf-8"))["phrases"]}
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8")); only = {x for x in a.only.split(",") if x}
    sb = (D / "分镜.md").read_text(encoding="utf-8"); vd = (D / "视频提示词.md").read_text(encoding="utf-8")
    report = []
    for n, s in spec["shots"].items():
        if only and n not in only:
            continue
        tests = []

        def ph(i):
            if i not in led:
                raise SystemExit(f"{n}: 장부에 없는 id {i}")
            if led[i]["status"] == "bad":
                raise SystemExit(f"{n}: 장부에서 실패로 적힌 말 {i} — {led[i].get('evidence')}")
            if led[i]["status"] == "test":
                tests.append(i)
            return led[i]["en"]
        cam = s.get("camera", {})
        framing = [ph(cam[k]) for k in ("1_size", "2_height") if cam.get(k)]
        parts = [HEAD]
        who = s.get("characters") or ([first] if first and (s.get("figure", True) or s.get("hands")) else [])
        for cid in who:
            if cid not in chars:
                raise SystemExit(f"{n}: 설정에 없는 인물 id {cid}")
            c = chars[cid]
            if s.get("figure", True) and (c.get("video_sentence") or "").strip():
                parts.append(sent(c["video_sentence"]))
            if s.get("hands") and (c.get("hands_sentence") or "").strip():
                parts.append(sent(c["hands_sentence"]))
        if s.get("framing"):  # 장부에 없는 화면 문장(그림과 맞추기) — 시험 말로 센다
            parts.append(sent(s["framing"])); tests.append("framing")
        elif framing:
            parts.append(sent("Framing: " + ", ".join(framing)))
        parts.append(sent(s["action"]))
        for k in ("3_relation", "4_move", "5_lens", "6_end"):
            if cam.get(k) and not led.get(cam[k], {}).get("en", "").startswith("("):
                parts.append(sent(ph(cam[k])))
            elif cam.get(k):
                ph(cam[k])
        for f in s.get("free", []):
            parts.append(sent(f)); tests.append("free")
        for g in s.get("guards", []):
            parts.append(sent(ph(g)))
        snd = s.get("sound", "")
        if s.get("sound_id"):
            snd = ph(s["sound_id"]) + (", " + snd if snd else "")
        parts.append(sent(f"Audio: {snd}, close and clear" if snd else "Audio: quiet room tone, close and clear"))
        parts.append(TAIL)
        prompt = " ".join(p for p in parts if p)
        gen = s["gen"]; start = s["start"]
        if gen == "start_end" or cam.get("4_move") == "MOVE-PUSH-END":
            if gen != "start_end":
                raise SystemExit(f"{n}: MOVE-PUSH-END는 gen=start_end와 함께 쓴다")
        ctl = "起始构图、人物位置与姿态、光线与色调" + ("、首尾同帧" if gen == "hold" else "")
        rl = f"REF-START（顺序：1）· {start}《本镜起始帧》（用途：起始帧；控制：{ctl}；不得控制：画外空间、下一镜构图）"
        state = {"start": "仅首帧（촬영 지시서 — 끝 프레임 없음）", "hold": "首尾同帧（촬영 지시서 — hold_last_frame）",
                 "start_end": "首尾不同帧（촬영 지시서 — 끝 = 시작의 88% 자르기" + (" — 자리 " + str(s["end_crop"]) if s.get("end_crop") else ", 가운데") + "）"}[gen]
        if gen == "start_end":
            end = s.get("end") or start.replace("/images/", "/images/_push/").replace(".png", "_push88.png")
            if end == start:
                raise SystemExit(f"{n}: 끝 프레임 경로가 시작 그림과 같다 — 지시서에 end를 적는다")
            box = s.get("end_crop", [0.06, 0.06, 0.88, 0.88])  # [x, y, w, h] 비율
            rl += f"；REF-END（顺序：2）· {end}《本镜结束帧（推近）》（用途：结束帧；控制：终点构图；不得控制：起始姿态、中间动作）"
            if not a.dry and not (s.get("end") and (P / end).exists()):
                (P / end).parent.mkdir(parents=True, exist_ok=True)
                w, h = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                                str(P / start)], capture_output=True, text=True, check=True).stdout.strip().split(","))
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(P / start), "-vf",
                                f"crop=iw*{box[2]}:ih*{box[3]}:iw*{box[0]}:ih*{box[1]},scale={w}:{h}:flags=lanczos", str(P / end)], check=True)
        m = re.search(rf"^## MOTION-{a.ep}-{n} · .*?(?=^## |\Z)", vd, re.M | re.S)
        if not m:
            raise SystemExit(f"{n}: 视频提示词에 MOTION 항목 없음")
        sec = m.group(0); nv = sec
        nv = re.sub(r"^- 时长：.*$", f"- 时长：{s['duration']}秒", nv, count=1, flags=re.M)
        nv = re.sub(r"^- 输入参考图：.*$", lambda _m: "- 输入参考图：" + rl, nv, count=1, flags=re.M)
        nv = re.sub(r"^- 起始帧：.*$", "- 起始帧：" + start, nv, count=1, flags=re.M)
        nv = re.sub(r"^- 状态链：.*$", "- 状态链：" + state, nv, count=1, flags=re.M)
        nv = re.sub(r"^- 终点：.*$", "- 终点：" + ("(시작과 같음 — 움직임 작게)" if gen == "hold" else "(끝 = 시작 가운데 88%)" if gen == "start_end" else "(시작 프레임만 — 끝 상태는 문장)"), nv, count=1, flags=re.M)
        nv = re.sub(r"^> .*$", lambda _m: "> " + prompt, nv, count=1, flags=re.M)
        vd = vd.replace(sec, nv, 1)
        m = re.search(rf"^## SHOT-{a.ep}-{n} · .*?(?=^## |\Z)", sb, re.M | re.S); sec = m.group(0); ns = sec
        fields = dict(s.get("sb", {})); fields["时长"] = fields.get("时长", f"{s['duration']}秒（촬영 지시서）")
        fields["终点"] = "시작과 같은 화면(hold)." if gen == "hold" else "동작이 끝난 상태(시작 프레임만 — 끝 상태는 영상 문장)" if gen == "start" else "같은 자리, 화면은 약 12% 가까이(끝 프레임)"
        if s.get("mode"):
            fields["시점"] = s["mode"]
        for k, v in fields.items():
            if re.search(rf"^- {k}：", ns, re.M):
                ns = re.sub(rf"^- {k}：.*$", lambda _m: f"- {k}：{v}", ns, count=1, flags=re.M)
            else:
                ns = ns.replace("- 唯一动作：", f"- {k}：{v}\n- 唯一动作：", 1)
        ns = re.sub(r"^- 输入参考图：.*$", lambda _m: "- 输入参考图：" + rl, ns, count=1, flags=re.M)
        sb = sb.replace(sec, ns, 1)
        report.append({"shot": n, "gen": gen, "tests": tests, "chars": len(prompt), **({"prompt": prompt} if a.dry else {})})
    stale = {}
    for ovp in sorted((D / "制作成果" / "成片").glob("preview_overrides*.json")):
        ov = json.loads(ovp.read_text(encoding="utf-8"))
        for r in report:
            hits = [k for k in ("vf", "take", "cut", "shot_dur", "split") if r["shot"] in ov.get(k, {})]
            hits += [f"trim_head:{f}" for f in ov.get("trim_head", {}) if f"-{r['shot']}_" in f]
            if hits:
                stale.setdefault(ovp.name, {})[r["shot"]] = hits
    if stale:
        print("옛 손질 확인(새 문장·그림이면 지우거나 다시 정할 것 — R-MOV-07):", json.dumps(stale, ensure_ascii=False))
    if not a.dry:
        (D / "分镜.md").write_text(sb, encoding="utf-8"); (D / "视频提示词.md").write_text(vd, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
