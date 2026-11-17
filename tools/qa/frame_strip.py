#!/usr/bin/env python3
"""영상 → 프레임 띠 그림(검수 에이전트가 영상을 '보는' 방법). ffmpeg만 쓴다.

python3 <TOOLS>/qa/frame_strip.py <video.mp4> [--n 8] [--crop 0.2,0,0.6,0.45] [--diff] [--out 띠.png]

- 위 줄: 영상 전체에 고르게 n장(시간 표시). 얼굴·손·소품이 프레임마다 그대로인지 본다.
- --crop x,y,w,h(비율): 머리·손 같은 한 부분만 크게(예: 머리 프레임 띠).
- --diff: 아래 줄에 이웃 프레임 차이(밝을수록 많이 바뀜) — 원인 없는 변화·튐을 찾는다.
- 기본 출력: <영상 폴더>/_strips/<영상 이름>[_crop][_diff].png
"""
import argparse, subprocess
from pathlib import Path


def dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--n", type=int, default=8); ap.add_argument("--crop")
    ap.add_argument("--diff", action="store_true"); ap.add_argument("--out"); ap.add_argument("--width", type=int, default=180)
    a = ap.parse_args()
    v = Path(a.video).resolve(); d = dur(v)
    if d <= 0:
        raise SystemExit("길이를 못 읽음")
    suffix = ("_crop" if a.crop else "") + ("_diff" if a.diff else "")
    out = Path(a.out) if a.out else v.parent / "_strips" / f"{v.stem}{suffix}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = max(a.n / d, 0.01)
    pre = ""
    if a.crop:
        x, y, w, h = [float(t) for t in a.crop.split(",")]
        pre = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},"
    label = "drawtext=text='%{pts\\:hms}':x=4:y=4:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.5,"
    top = f"[0:v]{pre}fps={rate:.5f},scale={a.width}:-2,{label}tile={a.n}x1[top]"
    if a.diff:
        bot = f"[1:v]{pre}tblend=all_mode=difference,eq=brightness=0.05:contrast=4,fps={rate:.5f},scale={a.width}:-2,tile={a.n}x1[bot]"
        fc = f"{top};{bot};[top][bot]vstack[o]"
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(v), "-i", str(v), "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", str(out)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(v), "-filter_complex", top, "-map", "[top]", "-frames:v", "1", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and "drawtext" in r.stderr:  # drawtext(freetype) 없는 ffmpeg — 시간 표시 없이
        cmd = [c.replace(label, "") for c in cmd]
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr.strip()[-500:])
    print(out)


if __name__ == "__main__":
    main()
