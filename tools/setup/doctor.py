#!/usr/bin/env python3
"""환경 점검 — 이 컴퓨터에서 shortform-desk가 돌 준비가 됐는지 본다. 키 값은 절대 출력하지 않는다(있다/없다만).

python3 <TOOLS>/setup/doctor.py                 # 점검 + 어댑터 설정 파일을 지금 플러그인 경로로 맞춤
python3 <TOOLS>/setup/doctor.py --install-venv  # cv2·numpy·Pillow 가상환경 만들기(키프레임 크기·움직임 검사용)
python3 <TOOLS>/setup/doctor.py --selftest      # 어댑터 4개 오프라인 모의 시험까지
python3 <TOOLS>/setup/doctor.py --json

어댑터 설정: ~/.config/shortform-desk/adapters.json (프로젝트 밖. 키는 넣지 않는다 — 어댑터가 환경변수/키체인에서 읽는다)
"""
import argparse, json, os, platform, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

CONFIG = Path(os.environ.get("SHORTFORM_ADAPTER_CONFIG", Path.home() / ".config" / "shortform-desk" / "adapters.json"))
KEYS = {"SUME_API_KEY": "sume 영상(sume-video)", "ELEVENLABS_API_KEY": "ElevenLabs 내레이션(elevenlabs-tts)",
        "GEMINI_API_KEY": "Gemini 이미지·영상(gemini-image / gemini-video, 대체용)"}


def has_key(name):
    if os.environ.get(name):
        return "환경변수"
    if platform.system() == "Darwin":
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", name], capture_output=True)
        if r.returncode == 0:
            return "키체인"
    return None


def adapter_config():
    py = sys.executable
    A = common.TOOLS / "adapters"
    return {"adapters": {
        "codex-image": {"command": [py, str(A / "codex_image_adapter.py")], "timeout_seconds": 1000},
        "sume-video": {"command": [py, str(A / "sume_video_adapter.py")], "timeout_seconds": 1800,
                       "reference_roles": ["first_frame", "last_frame", "reference_image"]},
        "elevenlabs-tts": {"command": [py, str(A / "elevenlabs_tts_adapter.py")], "timeout_seconds": 600},
        "gemini-image": {"command": [py, str(A / "gemini_adapter.py"), "image"], "timeout_seconds": 900},
        "gemini-video": {"command": [py, str(A / "gemini_adapter.py"), "video"], "timeout_seconds": 2400,
                         "reference_roles": ["first_frame", "last_frame", "reference_image", "reference_video"]},
    }}


def write_config():
    want = adapter_config()
    old = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    merged = dict(old); merged.setdefault("adapters", {})
    merged["adapters"] = {**old.get("adapters", {}), **want["adapters"]}  # 사용자가 더한 다른 어댑터는 둔다
    if merged != old:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "갱신"
    return "그대로"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install-venv", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    R = []

    def add(name, ok, detail, need="필수"):
        R.append({"item": name, "ok": bool(ok), "detail": detail, "need": need})

    add("파이썬 3.9+", sys.version_info >= (3, 9), sys.version.split()[0])
    for tool in ("ffmpeg", "ffprobe"):
        add(tool, shutil.which(tool), shutil.which(tool) or "없음 — macOS: brew install ffmpeg")
    if shutil.which("ffmpeg"):
        f = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        add("ffmpeg drawtext", " drawtext " in f, "프레임 띠 시간 표시(없어도 됨)", "선택")
        add("ffmpeg loudnorm", " loudnorm " in f, "음량 맞춤")
    if a.install_venv and not (common.VENV / "bin" / "python3").exists():
        common.VENV.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(common.VENV)], check=True)
        subprocess.run([str(common.VENV / "bin" / "pip"), "install", "-q", "opencv-python-headless", "numpy", "Pillow"], check=True)
    cv = common.cv2_python()
    add("cv2 파이썬(움직임·템포·색 맞춤 검사)", cv, cv or "없음 — doctor.py --install-venv", "권장")
    codex = shutil.which("codex")
    if codex:
        st = subprocess.run([codex, "login", "status"], capture_output=True, text=True)
        out = (st.stdout + st.stderr).strip().splitlines()
        add("Codex CLI 로그인(이미지 codex-image)", st.returncode == 0 and "Logged in" in " ".join(out), out[0] if out else "?", "이미지에 필수")
    else:
        add("Codex CLI(이미지 codex-image)", False, "없음 — npm i -g @openai/codex 뒤 codex login(ChatGPT 계정)", "이미지에 필수")
    for k, what in KEYS.items():
        where = has_key(k)
        add(f"{k} — {what}", where, where or f"없음 — 터미널에서: security add-generic-password -a \"$USER\" -s {k} -w",
            "선택(대체용)" if k == "GEMINI_API_KEY" else "그 서비스에 필수")
    add("어댑터 설정", True, f"{CONFIG} ({write_config()})")
    add("drama-skills(동봉)", (common.PRODUCE_SCRIPTS / "production_tool.py").exists(), str(common.PLUGIN_ROOT / "skills"))
    if a.selftest:
        for name in ("codex_image_adapter", "sume_video_adapter", "elevenlabs_tts_adapter", "gemini_adapter"):
            r = subprocess.run([sys.executable, str(common.TOOLS / "adapters" / f"{name}.py"), "--selftest"], capture_output=True, text=True)
            add(f"모의 시험 {name}", r.returncode == 0, (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "")
    if a.json:
        print(json.dumps({"plugin_root": str(common.PLUGIN_ROOT), "tools": str(common.TOOLS), "adapter_config": str(CONFIG), "checks": R},
                         ensure_ascii=False, indent=1))
        return
    print(f"플러그인: {common.PLUGIN_ROOT}\n도구: {common.TOOLS}\n어댑터 설정: {CONFIG}\n")
    for r in R:
        print(f"{'✓' if r['ok'] else '✗'} [{r['need']}] {r['item']}: {r['detail']}")
    bad = [r for r in R if not r["ok"] and r["need"] == "필수"]
    print("\n준비 완료" if not bad else f"\n필수 {len(bad)}개가 빠졌습니다")


if __name__ == "__main__":
    main()
