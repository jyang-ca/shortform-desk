#!/usr/bin/env python3
"""끝까지 시험(돈 안 듦) — 가짜 시리즈로 설정 → 린트 → job 검증(prepare) → 미리보기 조립 → 자동 검사 → 촬영 지시 조립 → 보드.

python3 <TOOLS>/setup/e2e_test.py [--keep <폴더>]

외부 서비스는 부르지 않는다. 생성물 대신 ffmpeg로 만든 색 그림·시험 영상·신호음을 쓴다.
"""
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

T = common.TOOLS
PT = common.PRODUCE_SCRIPTS / "production_tool.py"
PY = sys.executable
OK = []


def run(*cmd, py=PY, check=True):
    r = subprocess.run([py, *map(str, cmd)], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"실패: {' '.join(map(str, cmd))}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def step(name, cond, detail=""):
    if not cond:
        raise SystemExit(f"✗ {name} {detail}")
    OK.append(name); print(f"✓ {name} {detail}")


ANSWERS = {
    "title": "빈 우산 가게", "slug": "umbrella", "logline": "비 오는 골목의 우산 가게 주인이 손님마다 두고 간 우산의 사연을 찾는다",
    "genre": "일상 미스터리", "format": {"narration": "first-person-vo", "max_seconds": 90, "episodes_planned": 10, "target_seconds": [30, 60],
                                     "first_shot_seconds": [2.0, 3.5], "chars_per_sec": [4.0, 9.0]},
    "look": {"style": "포토리얼 시네마틱", "style_line": "Photorealistic cinematic still, vertical 9:16, soft rain light."},
    "characters": [{"id": "C1", "name": "주인", "role": "주인공", "ref_label": "《主角·C1》", "prompt_words": ["shop owner", "owner"], "faceless": False,
                    "image_sentence": "A woman in her thirties with a short grey wool coat and a red scarf",
                    "video_sentence": "The shop owner wears a short grey wool coat and a red scarf.",
                    "hands_sentence": "Her hands are bare, with a thin silver ring on the left hand.", "locks": ["red scarf"]}],
    "locations": [{"id": "L1", "name": "우산 가게", "layout": "좁은 가게, 문은 오른쪽, 우산 꽂이는 왼쪽 벽"}],
    "production": {"voice_id": "TESTVOICE000000000000"},
}


def ref(slot, order, path, label, use):
    return f"{slot}（顺序：{order}）· {path}《{label}》（用途：{use}；控制：起始构图、人物位置与姿态、光线与色调；不得控制：画外空间、下一镜构图）"


def fixture(P: Path):
    E = P / "剧集" / "EP001"; M = E / "制作成果"
    for d in ("images", "video", "tts"):
        (M / d).mkdir(parents=True, exist_ok=True)
    (P / "设定集").mkdir(exist_ok=True)
    ff("-f", "lavfi", "-i", "color=c=0x556677:s=720x1280", "-frames:v", "1", P / "设定集" / "C1_sheet.png")
    for n, c in (("001", "0x334455"), ("002", "0x445566"), ("003", "0x665544")):
        ff("-f", "lavfi", "-i", f"color=c={c}:s=720x1280", "-vf", "drawbox=x=200:y=500:w=320:h=500:color=white@0.6:t=fill",
           "-frames:v", "1", M / "images" / f"SHOT-EP001-{n}_c01.png")
    for n in ("001", "002"):
        ff("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=24:d=4", "-f", "lavfi", "-i", "sine=f=220:d=4",
           "-vf", "zoompan=z='1+0.002*on':d=1:s=720x1280", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           M / "video" / f"MOTION-EP001-{n}_t01.mp4")
    for n, d in (("001", 2.2), ("003", 2.8)):  # '말' 대신 신호음(앞뒤 무음 포함)
        ff("-f", "lavfi", "-i", f"sine=f=440:d={d}", "-af", "adelay=200|200,apad=pad_dur=0.3,volume=0.3", "-ac", "1", M / "tts" / f"SHOT-EP001-{n}_vo01.mp3")
    img = ANSWERS["characters"][0]["image_sentence"]; vid = ANSWERS["characters"][0]["video_sentence"]; sty = ANSWERS["look"]["style_line"]
    sheet = ref("REF-C1", 1, "设定集/C1_sheet.png", "主角·C1", "人物参考")
    (E / "剧本.md").write_text("# EP001\n\n## EP001-SC001 가게 안, 비\n\n[VO] 나：비가 오면 손님이 온다.\n\n[VO] 나：오늘은 우산만 왔다.\n", encoding="utf-8")
    sb = ["# EP001 分镜", ""]
    shots = [("001", 3.0, "VO「비가 오면 손님이 온다.」", f"{img}, standing behind the counter of a narrow umbrella shop. {sty} No readable text, no logos, no watermark.",
              f"{sheet}；" + ref("REF-START", 2, "剧集/EP001/制作成果/images/SHOT-EP001-001_c01.png", "本镜起始帧", "起始帧")),
             ("002", 2.0, "빗소리", f"Close on a dripping black umbrella in the stand, a sexy red glow. {sty} No readable text, no logos, no watermark.",
              ref("REF-START", 1, "剧集/EP001/制作成果/images/SHOT-EP001-002_c01.png", "本镜起始帧", "起始帧")),
             ("003", 3.5, "VO「오늘은 우산만 왔다.」", f"{img}, looking at a lone umbrella on the counter. {sty}",
              f"{sheet}；PLAN-SHOT-START（顺序：2）· SHOT-EP001-003《本镜起始帧》（用途：起始帧）")]
    for n, d, snd, prompt, refs in shots:
        sb += [f"## SHOT-EP001-{n} · S{n} 시험 샷", "", "- 职责：시험", "- 来源：EP001-SC001", f"- 时长：{d}秒", "- 景别：미디엄",
               "- 唯一动作：작은 움직임", f"- 声音：{snd}", f"- 输入参考图：{refs}", "", "### 冻结关键帧提示词", "", f"> {prompt}", ""]
    (E / "分镜.md").write_text("\n".join(sb), encoding="utf-8")
    vd = ["# EP001 视频提示词", ""]
    for n, d in (("001", 3.0), ("002", 2.0)):
        start = f"剧集/EP001/制作成果/images/SHOT-EP001-{n}_c01.png"
        who = vid + " " if n == "001" else ""
        vd += [f"## MOTION-EP001-{n} · S{n}（视频）", "", f"- 分镜：SHOT-EP001-{n}", f"- 时长：{d}秒", "- 生成方式：图生视频",
               "- 输入参考图：" + ref("REF-START", 1, start, "本镜起始帧", "起始帧"), "- 静态视觉锚点：无（起始帧已绑定）",
               f"- 起始帧：{start}", "- 状态链：首尾同帧", "- 终点：(시작과 같음)", "", "### 可复制提示词", "",
               f"> Animate this start frame as one continuous shot. {who}Rain runs down the window. No on-screen text.", ""]
    (E / "视频提示词.md").write_text("\n".join(vd), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--keep")
    a = ap.parse_args()
    base = Path(a.keep).resolve() if a.keep else Path(tempfile.mkdtemp(prefix="shortform-e2e-"))
    base.mkdir(parents=True, exist_ok=True); P = base / "umbrella"
    if P.exists():
        shutil.rmtree(P)
    ans = base / "answers.json"; ans.write_text(json.dumps(ANSWERS, ensure_ascii=False), encoding="utf-8")

    r = json.loads(run(T / "setup" / "init_series.py", P, "--answers", ans).stdout)
    step("1 init_series", (P / "short-drama.json").exists() and (P / "시리즈" / "설정.json").exists() and (P / "CLAUDE.md").exists(),
         f"만든 것 {len(r['created'])} · 빈 칸 {[m['key'] for m in r['missing']]}")
    step("1b 규칙집·장부 복사", (P / "검수" / "규칙집_v01.md").exists() and (P / "검수" / "카메라문장_장부.json").exists())
    fixture(P)

    lint = json.loads(run(T / "qa" / "prompt_lint.py", P, "EP001").stdout)
    step("2 prompt_lint 금지 낱말 잡음", "SHOT-EP001-002" in lint["fail_items"], f"fail {lint['fail']} · warn {lint['warn']} · {lint['fail_items']}")
    step("2b 인물 라벨·문장 있는 샷은 통과", "SHOT-EP001-001" not in lint["fail_items"] and "MOTION-EP001-001" not in lint["fail_items"])
    t = json.loads(run(T / "qa" / "prompt_lint.py", P, "--text", "Close on the shop owner by the door.", "--kind", "video").stdout)
    step("2c 영상 문장에 인물 문장 없으면 fail", any(i["level"] == "fail" and i["rule"] == "R-ID-02" for i in t))

    jobs = base / "jobs"; shutil.rmtree(jobs, ignore_errors=True)
    run(T / "pipeline" / "make_jobs.py", P, "EP001", "video", jobs, "--take", "t02")
    run(T / "pipeline" / "make_tts_jobs.py", P, "EP001", jobs, "--take", "vo02")
    run(T / "pipeline" / "make_image_jobs.py", P, "EP001", jobs, "--candidate", "c02")
    made = sorted(jobs.glob("*.json"))
    prepared = 0
    for j in made:
        out = run(PT, "prepare", P, "--job", j)
        prepared += out.returncode == 0
    step("3 job 만들기 + drama-skills prepare", prepared == len(made) and len(made) == 5, f"{[j.stem for j in made]}")
    tts = json.loads(next(jobs.glob("SHOT-EP001-001_vo02.json")).read_text(encoding="utf-8"))
    step("3b TTS job에 설정 목소리·다음 줄", tts["parameters"]["voice_id"] == ANSWERS["production"]["voice_id"] and "next_text" in tts["parameters"])

    out = json.loads(run(T / "pipeline" / "assemble_preview.py", P, "EP001", "--version", "v01").stdout)
    step("4 미리보기 조립(분镜에서 샷표·정지 그림 대체)", (P / out["output"]).exists() and out["stills"] == ["003"], f"{out['seconds']}초 · 샷 {out['shots']}")
    qa = json.loads(run(T / "qa" / "preview_check.py", P, "EP001", "--version", "v01").stdout)
    step("5 미리보기 자동 검사", sum(qa["summary"].values()) > 10, json.dumps(qa["summary"], ensure_ascii=False))

    cv = common.cv2_python()
    if cv:
        tl = P / "剧集/EP001/制作成果/成片/EP001_미리보기_v01.json"
        vt = run(T / "qa" / "visual_tempo.py", P / "剧集/EP001/制作成果/成片/EP001_미리보기_v01.mp4", "--cuts", tl, py=cv)
        step("5b 시각 템포(cv2)", vt.returncode == 0, vt.stdout.strip().splitlines()[-1][:120] if vt.stdout.strip() else "")
        cg = P / "剧集/EP001/制作成果/成片/_검수/EP001_미리보기_v01_cut.json"
        step("5c 컷 문법(cv2, preview_check가 부름)", cg.exists())
    else:
        print("- 5b·5c 건너뜀: cv2 파이썬 없음(doctor.py --install-venv)")

    led = json.loads((P / "검수" / "카메라문장_장부.json").read_text(encoding="utf-8"))["phrases"]
    pick = lambda slot: next((x["id"] for x in led if x["slot"] == slot and x["status"] != "bad" and "PUSH" not in x["id"]), None)
    spec = {"shots": {"001": {"gen": "hold", "start": "剧集/EP001/制作成果/images/SHOT-EP001-001_c01.png", "duration": 3.0,
                              "action": "the owner lifts her eyes from the counter to the door", "characters": ["C1"],
                              "camera": {k: pick(k) for k in ("1_size", "2_height", "4_move") if pick(k)}, "sound": "soft rain on the window"}}}
    sp = base / "spec.json"; sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    dp = json.loads(run(T / "pipeline" / "dp_apply.py", P, "EP001", sp, "--dry").stdout.strip().splitlines()[-1])
    step("6 촬영 지시 조립(설정 인물 문장 들어감)", ANSWERS["characters"][0]["video_sentence"] in dp[0]["prompt"], f"시험 말 {dp[0]['tests']}")

    strip = run(T / "qa" / "frame_strip.py", P / "剧集/EP001/制作成果/video/MOTION-EP001-001_t01.mp4", "--n", "6", "--diff").stdout.strip()
    step("7 프레임 띠", Path(strip).exists(), Path(strip).name)

    with open(P / "검수" / "피드백-기록.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-01-01", "ep": "EP001", "targets": ["002"], "kind": "피드백", "quote": "#002 우산이 손잡이 없이 떠 있어",
                            "points": [{"text": "002 우산이 받침 없이 뜸", "class": "놓침", "rule": "R-MOV-09"}]}, ensure_ascii=False) + "\n")
    with open(P / "검수" / "지표.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-01-01", "ep": "EP001", "gate": "미리보기", "delivered": "v01", "auto_fixed": 0, "cost_usd": 0, "minutes": 1}) + "\n")
    run(T / "board" / "build_board.py", P)
    fb = (P / ".board" / "feedback.html").read_text(encoding="utf-8")
    step("8 보드 + 피드백·규칙 페이지", (P / ".board" / "index.html").exists() and "우산이 손잡이 없이" in fb and "R-MOV-09" in fb)

    print(f"\n모두 통과 ({len(OK)}단계). 시험 프로젝트: {P}")
    if not a.keep:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
