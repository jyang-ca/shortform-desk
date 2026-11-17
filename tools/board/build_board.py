#!/usr/bin/env python3
"""제작 보드 — drama-skills 프로젝트의 키프레임·영상을 샷별로 한눈에 보는 읽기 전용 HTML.

보드는 보기만 한다. 고르기·다시 만들기·생성은 채팅으로 한다.
프로젝트 파일은 읽기만 하고, 결과는 `<project>/.board/` 아래에만 쓴다.

읽는 것 (회차마다 `剧集/EPxxx/`)
  剧本.md            장면·내레이션 (샷 표가 없을 때 개요)
  分镜.md            `## SHOT-…` 샷, 冻结关键帧提示词, 输入参考图의 `用途：起始帧`(= 고른 키프레임)
  视频提示词.md       `## MOTION-…`, `分镜：SHOT-…`, 可复制提示词
  剪辑单.md           `来源：<MOTION|SHOT> · <경로>` (= 고른 테이크)
  制作成果/**         이미지·영상·음성. 파일 이름의 SHOT/MOTION ID로 샷에 묶는다(…_c01 후보, …_t01 테이크)
  制作成果/_검수.json  {"<프로젝트 상대 경로>": {"verdict": "통과|편집|다시", "note": "…", "by": "…"}}
  .short-drama/production/{jobs,runs}  생성 상태·어댑터·오류

Usage:  python3 <TOOLS>/board/build_board.py <project> [--episode EP001]
        (피드백·규칙 페이지 feedback.html도 함께 다시 만든다)

화면: 회차 미리보기(샷 타임라인과 연동) + 샷 카드(고른 키프레임·미리보기에 쓴 영상·판정·기록) + 크게 보기(←/→).
자동 새로고침 없음 — 보드를 다시 만든 뒤 페이지의 ↻ 버튼이나 브라우저 새로고침.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("build_board.py requires Python 3.9 or newer")

IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO = {".mp4", ".mov", ".webm"}
AUDIO = {".mp3", ".wav", ".m4a", ".aac"}
ID_RE = re.compile(r"(SHOT|MOTION)-(EP\d{3})-(\d{3}[A-Za-z]?)")
VARIANT_RE = re.compile(r"[_-]((?:c|t|kf)\d{2,3})", re.I)
REF_RE = re.compile(r"REF-[^（\s]+（顺序：\d+）·\s*(?P<path>[^《]+?)《(?P<label>[^》]*)》（用途：(?P<use>[^；）]+)")
VERDICT_CLASS = {"통과": "ok", "편집": "fix", "다시": "redo"}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def url_for(board_dir: Path, target: Path) -> str:
    rel = Path(*([".."] * len(board_dir.relative_to(board_dir.parents[0]).parts))) / target
    return urllib.parse.quote(str(rel).replace("\\", "/"))


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def sections(text: str, prefix: str) -> dict[str, dict]:
    """`## <ID> · 이름` 단위로 자른다. 값: name, fields(- 키：값), quotes(### 제목별 > 인용), refs."""
    out: dict[str, dict] = {}
    current = None
    sub = None
    for line in text.splitlines():
        m = re.match(rf"^## ({prefix}-EP\d{{3}}-\d{{3}}[A-Za-z]?)\s*(?:·\s*(.*))?$", line.strip())
        if m:
            current = {"name": (m.group(2) or "").strip(), "fields": {}, "quotes": {}, "refs": []}
            out[m.group(1)] = current
            sub = None
            continue
        if current is None:
            continue
        if line.startswith("## "):
            current = None
            continue
        if line.startswith("### "):
            sub = line[4:].strip()
            continue
        stripped = line.strip()
        if stripped.startswith(">") and sub:
            current["quotes"].setdefault(sub, []).append(stripped.lstrip("> ").rstrip())
        elif stripped.startswith("- ") and "：" in stripped:
            key, value = stripped[2:].split("：", 1)
            current["fields"][key.strip()] = value.strip()
            if "参考图" in key or key.strip() == "参考":
                current["refs"] = [m.groupdict() for m in REF_RE.finditer(value)]
    return out


def script_outline(text: str) -> list[dict]:
    scenes, current = [], None
    for line in text.splitlines():
        if line.startswith("## ") and re.match(r"^## EP\d{3}-SC\d{3}", line):
            current = {"title": line[3:].strip(), "vo": []}
            scenes.append(current)
        elif current is not None and line.startswith("[VO]"):
            current["vo"].append(line.split("：", 1)[-1].strip())
    return scenes


def production_index(project: Path) -> dict[str, dict]:
    """프로젝트 상대 출력 경로 → {adapter, job_id, status, error, finished}."""
    base = project / ".short-drama" / "production"
    index: dict[str, dict] = {}
    for job_file in sorted((base / "jobs").glob("*.json")):
        try:
            job = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        latest = None
        run_dir = base / "runs" / job_file.stem
        for run_file in sorted(run_dir.glob("*.json")) if run_dir.is_dir() else []:
            try:
                run = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if latest is None or str(run.get("started_at")) > str(latest.get("started_at")):
                latest = run
        if latest is None:  # prepared but never run (e.g. superseded plan) — not shown on the board
            continue
        for out in job.get("outputs", []):
            index[str(out)] = {
                "adapter": job.get("adapter"), "job_id": job.get("job_id"),
                "status": (latest or {}).get("status", "준비됨"),
                "error": ((latest or {}).get("error") or {}).get("code"),
                "finished": (latest or {}).get("finished_at"),
            }
    return index


def poster_for(board: Path, video: Path) -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    key = hashlib.sha1(f"{video}:{video.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    out = board / "cache" / f"poster_{key}.jpg"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(video), "-frames:v", "1", "-vf", "scale=-2:640", str(out)],
                       capture_output=True, check=False)
    return f"cache/{out.name}" if out.exists() else None


def strip_for(project: Path, board: Path, video: Path) -> str | None:
    """영상 머리 확인용 프레임 띠(5장). ffmpeg가 없으면 None."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    key = hashlib.sha1(f"{video}:{video.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    out = board / "cache" / f"strip_{key}.jpg"
    if out.exists():
        return f"cache/{out.name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, check=False)
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        return None
    times = [round(duration * f, 3) for f in (0.0, 0.25, 0.5, 0.75, 0.97)]
    args = [ffmpeg, "-v", "error", "-y"]
    for t in times:
        args += ["-ss", str(t), "-i", str(video)]
    chain = ";".join(f"[{i}:v]scale=-2:200,trim=end_frame=1[v{i}]" for i in range(len(times)))
    chain += ";" + "".join(f"[v{i}]" for i in range(len(times))) + f"hstack=inputs={len(times)}[o]"
    done = subprocess.run(args + ["-filter_complex", chain, "-map", "[o]", "-frames:v", "1", str(out)],
                          capture_output=True, check=False)
    return f"cache/{out.name}" if done.returncode == 0 and out.exists() else None


def collect_episode(project: Path, board: Path, ep_dir: Path, prod: dict[str, dict]) -> dict:
    ep = ep_dir.name
    shots = sections(read(ep_dir / "分镜.md"), "SHOT")
    motions = sections(read(ep_dir / "视频提示词.md"), "MOTION")
    cuts_text = read(ep_dir / "剪辑单.md")
    chosen_takes = {m.group(1).strip() for m in re.finditer(r"来源：\s*\S+\s*·\s*(\S+)", cuts_text)}
    verdicts: dict[str, dict] = {}
    try:
        verdicts = json.loads(read(ep_dir / "制作成果" / "_검수.json") or "{}")
    except ValueError:
        verdicts = {}
    rows: dict[str, dict] = {}

    def row(num: str) -> dict:
        return rows.setdefault(num, {"num": num, "shot": None, "motion": None, "images": [], "videos": [], "audio": []})

    for sid, data in shots.items():
        row(ID_RE.search(sid).group(3))["shot"] = (sid, data)
    for mid, data in motions.items():
        linked = ID_RE.search(data["fields"].get("分镜", "")) or ID_RE.search(mid)
        row(linked.group(3))["motion"] = (mid, data)
    media_root = ep_dir / "制作成果"
    for path in sorted(media_root.rglob("*")) if media_root.is_dir() else []:
        if not path.is_file() or path.name.startswith(("_", ".")):
            continue
        m = ID_RE.search(path.name)
        if not m:
            continue
        rel = str(path.relative_to(project))
        variant = VARIANT_RE.search(path.stem)
        item = {"path": path, "rel": rel, "variant": variant.group(1).lower() if variant else "",
                "prod": prod.get(rel, {}), "verdict": verdicts.get(rel, {}),
                "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")}
        suffix = path.suffix.lower()
        r = row(m.group(3))
        if suffix in IMAGE:
            r["images"].append(item)
        elif suffix in VIDEO:
            item["strip"] = strip_for(project, board, path)
            item["poster"] = poster_for(board, path)
            item["chosen"] = rel in chosen_takes
            r["videos"].append(item)
        elif suffix in AUDIO:
            r["audio"].append(item)
    existing = {i["rel"] for r in rows.values() for i in r["images"] + r["videos"] + r["audio"]}
    for rel, info in prod.items():
        m = ID_RE.search(rel)
        if not rel.startswith(f"剧集/{ep}/") or rel in existing or not m:
            continue
        r = row(m.group(3))
        r.setdefault("jobs", []).append({"rel": rel, "status": info["status"], "error": info.get("error"),
                                         "adapter": info.get("adapter"), "kind": Path(rel).suffix.lower()})
    for r in rows.values():
        selected = set()
        if r["shot"]:
            selected = {ref["path"].strip() for ref in r["shot"][1]["refs"] if ref["use"].strip() == "起始帧"}
        for img in r["images"]:
            img["chosen"] = img["rel"] in selected
        known = {img["rel"] for img in r["images"]}
        for rel in sorted(selected - known):  # 起始帧 bound to a file outside 制作成果 (e.g. a reused earlier frame)
            path = project / rel
            if path.is_file() and path.suffix.lower() in IMAGE:
                r["images"].insert(0, {"path": path, "rel": rel, "variant": "지정", "prod": {}, "verdict": verdicts.get(rel, {}),
                                       "chosen": True,
                                       "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")})
    pending = [p for p, info in prod.items() if p.startswith(f"剧集/{ep}/") and info["status"] not in ("succeeded",)]
    allcuts = sorted((media_root / "成片").glob("*.mp4"), key=lambda q: q.stat().st_mtime) if (media_root / "成片").is_dir() else []
    cuts = [q for q in allcuts if re.search(r"_v\d+\.mp4$", q.name)] or allcuts  # 기본 판(뒤에 글자 없는 판)이 맨 앞
    cut = None
    if cuts:
        c = cuts[-1]; meta = {}
        alts = [q for q in allcuts if re.search(rf"{re.escape(c.stem)}[a-z]+\.mp4$", q.name)]  # 같은 판의 다른 버전(예: v11p = 카메라 밀기 판)
        try:
            meta = json.loads(c.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        cut = {"rel": str(c.relative_to(project)), "name": c.name, "meta": meta, "alts": [str(q.relative_to(project)) for q in alts],
               "mtime": dt.datetime.fromtimestamp(c.stat().st_mtime).strftime("%m-%d %H:%M")}
    return {"ep": ep, "cut": cut, "rows": [rows[k] for k in sorted(rows)], "outline": script_outline(read(ep_dir / "剧本.md")),
            "docs": {name: (ep_dir / name).exists() for name in ("剧本.md", "分镜.md", "视频提示词.md", "剪辑单.md")},
            "pending": pending}


def thumb_for(board: Path, image: Path) -> str | None:
    """키프레임 썸네일(세로 480px jpg, 캐시)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    key = hashlib.sha1(f"{image}:{image.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    out = board / "cache" / f"thumb_{key}.jpg"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(image), "-vf", "scale=-2:480", "-q:v", "3", str(out)],
                       capture_output=True, check=False)
    return f"cache/{out.name}" if out.exists() else None


def pack_zip(project: Path, ep: str, cut_name: str):
    try:  # 미리보기에 들어간 클립 zip(미리보기가 더 새로우면 다시 묶는다)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pack_clips", Path(__file__).with_name("pack_clips.py"))
        pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
        ver = re.search(r"_(v\d+[a-z]*)\.mp4$", cut_name)
        return pc.pack(project, ep, ver.group(1) if ver else None)
    except Exception:  # 보드는 묶기에 실패해도 그려진다
        return None


def episode_data(project: Path, board: Path, e: dict) -> dict:
    """화면에 넘길 회차 데이터(JSON). 미디어 경로는 보드 기준 상대 URL."""
    u = lambda rel: url_for(board, Path(rel))
    cut = e.get("cut"); meta = (cut or {}).get("meta") or {}
    tl = {x["shot"]: x for x in meta.get("shots", [])}
    in_preview = {str(x.get("source", "")).split(" (")[0].replace("정지 화면 ", "").strip() for x in meta.get("shots", [])}
    shots = []
    for r in e["rows"]:
        sid, sdata = r["shot"] or (f"SHOT-{e['ep']}-{r['num']}", {"name": "", "fields": {}, "quotes": {}})
        t = tl.get(r["num"], {})
        imgs = []
        for i in sorted(r["images"], key=lambda x: x["path"].stat().st_mtime):
            v = i.get("verdict") or {}
            imgs.append({"kind": "image", "src": u(i["rel"]), "thumb": thumb_for(board, i["path"]), "name": i["path"].name,
                         "variant": i["variant"] or "원본", "chosen": bool(i.get("chosen")), "verdict": v.get("verdict", ""),
                         "note": v.get("note", ""), "by": v.get("by", ""), "rules": v.get("rules", []), "time": i["mtime"],
                         "adapter": (i.get("prod") or {}).get("adapter") or ""})
        vids = []
        for i in sorted(r["videos"], key=lambda x: x["variant"]):
            v = i.get("verdict") or {}
            vids.append({"kind": "video", "src": u(i["rel"]), "poster": i.get("poster"), "strip": i.get("strip"),
                         "name": i["path"].name, "variant": i["variant"] or "테이크", "used": i["rel"] in in_preview,
                         "verdict": v.get("verdict", ""), "note": v.get("note", ""), "by": v.get("by", ""),
                         "rules": v.get("rules", []), "time": i["mtime"]})
        jobs = [{"name": Path(j["rel"]).name, "status": j["status"], "error": j.get("error") or ""} for j in r.get("jobs", [])]
        f = sdata["fields"]
        shots.append({
            "num": r["num"], "id": sid, "code": t.get("code", ""), "name": sdata.get("name", ""),
            "narration": t.get("narration") or "", "start": t.get("start"), "dur": t.get("duration") or (float(re.search(r"[\d.]+", f["时长"]).group(0)) if re.search(r"[\d.]+", f.get("时长", "")) else None),
            "still": str(t.get("source", "")).startswith("정지"),
            "kprompt": " ".join(sdata["quotes"].get("冻结关键帧提示词", [])),
            "mprompt": " ".join(r["motion"][1]["quotes"].get("可复制提示词", [])) if r["motion"] else "",
            "images": imgs, "videos": vids, "jobs": jobs})
    out = {"ep": e["ep"], "shots": shots, "outline": e["outline"], "docs": e["docs"]}
    if cut:
        z = pack_zip(project, e["ep"], cut["name"])
        alts = []
        for rel in cut.get("alts", []):
            q = project / rel; za = pack_zip(project, e["ep"], q.name)
            alts.append({"src": u(rel), "name": q.name, "label": "카메라 밀기 판" if q.stem.endswith("p") else q.stem.rsplit("_", 1)[-1],
                         "zip": {"src": u(za.relative_to(project)), "mb": round(za.stat().st_size / 1e6)} if za else None})
        out["preview"] = {"src": u(cut["rel"]), "poster": poster_for(board, project / cut["rel"]), "name": cut["name"],
                          "time": cut["mtime"], "seconds": meta.get("total_seconds"), "tempo": meta.get("tempo"),
                          "zip": {"src": u(z.relative_to(project)), "mb": round(z.stat().st_size / 1e6)} if z else None, "alts": alts}
    return out


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__ 제작 보드</title>
<style>
:root{--bg:#0e0f12;--panel:#16181d;--card:#1b1e24;--line:#2a2e36;--ink:#eceef2;--mute:#8b919c;--faint:#5c626d;
--ok:#3ecf8e;--fix:#f2b134;--redo:#ff6b6b;--acc:#7aa2ff;--accbg:rgba(122,162,255,.14);--r:12px}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",sans-serif}
a{color:inherit}button{font:inherit;color:inherit;cursor:pointer}
.top{position:sticky;top:0;z-index:20;background:rgba(14,15,18,.86);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.top .in{max-width:1480px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:-.01em;font-size:15px}.brand small{color:var(--mute);font-weight:500;margin-left:6px}
.tabs{display:flex;gap:4px}.tab{padding:5px 12px;border-radius:999px;border:1px solid var(--line);background:none;color:var(--mute);text-decoration:none;font-size:13px}
.tab.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}.sp{flex:1}
.meta{color:var(--mute);font-size:12px}.ghost{padding:5px 10px;border-radius:8px;border:1px solid var(--line);background:none;color:var(--mute);font-size:12px;text-decoration:none}
.ghost:hover{color:var(--ink);border-color:var(--faint)}
main{max-width:1480px;margin:0 auto;padding:20px}
.hero{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:20px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:22px}
@media(max-width:860px){.hero{grid-template-columns:1fr}}
.player video{width:100%;aspect-ratio:9/16;background:#000;border-radius:10px;display:block}
.hinfo{min-width:0}.hinfo h2{margin:0 0 4px;font-size:20px;letter-spacing:-.02em}.hinfo .meta{margin-bottom:12px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 14px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:92px}
.stat b{display:block;font-size:20px;line-height:1.2}.stat span{color:var(--mute);font-size:12px}
.stat.ok b{color:var(--ok)}.stat.fix b{color:var(--fix)}.stat.redo b{color:var(--redo)}
.dl{display:inline-flex;align-items:center;gap:8px;padding:9px 14px;border-radius:10px;background:var(--acc);color:#0b1020;font-weight:700;text-decoration:none}
.bar{display:flex;height:8px;border-radius:6px;overflow:hidden;margin:16px 0 8px;background:var(--line)}.bar i{display:block;height:100%;border-right:1px solid var(--panel);cursor:pointer}
.bar i.cur{outline:2px solid var(--ink);outline-offset:-2px}
.strip{display:flex;gap:6px;overflow-x:auto;padding:4px 2px 10px;scroll-behavior:smooth}
.strip button{flex:0 0 auto;width:62px;padding:0;border:2px solid transparent;border-radius:8px;background:none;position:relative}
.strip img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:6px;display:block;background:#000}
.strip .n{position:absolute;left:4px;top:3px;font-size:10px;font-weight:700;text-shadow:0 1px 2px #000}
.strip .dot{position:absolute;right:4px;top:5px;width:7px;height:7px;border-radius:50%}
.strip button.cur{border-color:var(--acc)}.strip .nar{color:var(--mute);font-size:12px;margin-top:2px}
.now{min-height:42px;font-size:13px;color:var(--mute)}.now b{color:var(--ink)}
.vers{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}.filters{display:flex;gap:6px;align-items:center;margin:0 0 14px;flex-wrap:wrap}.chip{padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:none;color:var(--mute);font-size:13px}
.chip.on{background:var(--accbg);color:var(--acc);border-color:var(--acc)}.chip em{font-style:normal;opacity:.7;margin-left:4px}
.list{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:14px}@media(max-width:460px){.list{grid-template-columns:1fr}}
.shot{background:var(--card);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;scroll-margin-top:80px}
.shot.flash{box-shadow:0 0 0 2px var(--acc)}
.sh{display:flex;align-items:center;gap:10px;padding:12px 14px 8px}
.num{font-weight:800;font-size:18px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.code{color:var(--mute);font-size:12px;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.v{font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px}.v.ok{background:rgba(62,207,142,.15);color:var(--ok)}.v.fix{background:rgba(242,177,52,.15);color:var(--fix)}
.v.redo{background:rgba(255,107,107,.15);color:var(--redo)}.v.none{background:var(--line);color:var(--mute)}
.sh .go{margin-left:auto}.nar2{padding:0 14px 10px;font-size:14px;color:#d6dae2;min-height:2.6em}.nar2.empty{color:var(--faint);font-style:italic}
.main{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:0 14px;max-width:420px}
.slot{position:relative}.slot .lbl{font-size:11px;color:var(--mute);margin:0 0 4px;display:flex;gap:6px;align-items:center}
.media{position:relative;width:100%;aspect-ratio:9/16;border-radius:10px;overflow:hidden;background:#000;border:0;padding:0;display:block}
.media img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s}.media:hover img{transform:scale(1.02)}
.media .play{position:absolute;inset:0;display:grid;place-items:center}.media .play span{width:46px;height:46px;border-radius:50%;background:rgba(0,0,0,.55);display:grid;place-items:center;font-size:18px;backdrop-filter:blur(4px)}
.media .tag{position:absolute;left:8px;bottom:8px;font-size:11px;background:rgba(0,0,0,.6);padding:2px 7px;border-radius:6px}
.empty-slot{aspect-ratio:9/16;border:1px dashed var(--line);border-radius:10px;display:grid;place-items:center;color:var(--faint);font-size:12px;text-align:center;padding:10px}
.note{padding:10px 14px 0;font-size:12px;color:var(--mute)}.note b{color:var(--ink);font-weight:600}
.hist{padding:10px 14px 14px}.hist h4{margin:0 0 6px;font-size:11px;color:var(--mute);font-weight:600;letter-spacing:.02em}
.thumbs{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}
.th{flex:0 0 auto;width:54px;position:relative;border:2px solid transparent;border-radius:7px;padding:0;background:none}
.th img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:5px;display:block;background:#000;opacity:.8}.th:hover img{opacity:1}
.th.cur{border-color:var(--acc)}.th .dot{position:absolute;right:3px;top:3px;width:8px;height:8px;border-radius:50%;border:1px solid rgba(0,0,0,.5)}
.th .k{position:absolute;left:3px;bottom:3px;font-size:9px;background:rgba(0,0,0,.65);padding:0 4px;border-radius:4px}
.dot.ok{background:var(--ok)}.dot.fix{background:var(--fix)}.dot.redo{background:var(--redo)}.dot.none{background:var(--faint)}
details.pr{padding:0 14px 14px;font-size:12px;color:var(--mute)}details.pr summary{cursor:pointer}details.pr p{white-space:pre-wrap;margin:6px 0}
.job{margin:0 14px 10px;font-size:12px;color:var(--redo)}
.lb{position:fixed;inset:0;z-index:50;background:rgba(5,6,8,.94);display:none;grid-template-columns:1fr 360px}
.lb.open{display:grid}@media(max-width:900px){.lb{grid-template-columns:1fr;grid-template-rows:1fr auto}}
.lb .stage{display:grid;place-items:center;padding:24px;min-height:0}.lb .stage img,.lb .stage video{max-width:100%;max-height:calc(100vh - 48px);border-radius:10px;background:#000}
.lb .side{background:var(--panel);border-left:1px solid var(--line);padding:20px;overflow:auto}
.lb .side h3{margin:0 0 4px}.lb .side .stripimg{width:100%;border-radius:6px;margin:10px 0}
.lb .x{position:absolute;right:16px;top:12px;font-size:22px;background:none;border:0;color:var(--mute)}
.lb .nav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(255,255,255,.08);border:0;width:44px;height:64px;border-radius:10px;font-size:22px;color:var(--ink)}
.lb .prev{left:12px}.lb .next{right:372px}@media(max-width:900px){.lb .next{right:12px}}
.kv{display:grid;grid-template-columns:70px 1fr;gap:4px 10px;font-size:13px;margin-top:12px}.kv dt{color:var(--mute)}.kv dd{margin:0}
.lb .thumbs{margin-top:14px;flex-wrap:wrap}
.outline li{margin-bottom:8px}
</style></head><body>
<header class="top"><div class="in"><span class="brand">__TITLE__<small>제작 보드</small></span><nav class="tabs" id="tabs"></nav><span class="sp"></span>
__FEEDBACK__<span class="meta">만든 시각 __BUILT__ · 보기 전용, 조작은 채팅으로</span><button class="ghost" onclick="location.reload()">↻ 새로고침</button></div></header>
<main id="app"></main>
<div class="lb" id="lb"><div class="stage" id="lbStage"></div><aside class="side" id="lbSide"></aside>
<button class="x" onclick="closeLB()">✕</button><button class="nav prev" onclick="stepLB(-1)">‹</button><button class="nav next" onclick="stepLB(1)">›</button></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const EPS=JSON.parse(document.getElementById('data').textContent);
const VC={'통과':'ok','편집':'fix','다시':'redo'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let cur=null, filter='all', lbItems=[], lbIdx=0;
function vclass(v){return VC[v]||'none'}
function shotState(s){ // 대표 판정: 지금 쓰는 영상 > 고른 키프레임
  const used=s.videos.find(v=>v.used); const ch=s.images.find(i=>i.chosen);
  if(s.still||!used) return {v:'영상 없음',c:'redo'};
  const vv=used.verdict||'', iv=ch?ch.verdict:'';
  const w=[vv,iv].includes('다시')?'다시':[vv,iv].includes('편집')?'편집':(vv||iv)?'통과':'';
  return {v:w||'미검수',c:w?vclass(w):'none'};
}
function thumbOf(s){const ch=s.images.find(i=>i.chosen)||s.images[s.images.length-1];const u=s.videos.find(v=>v.used);
  return (ch&&(ch.thumb||ch.src))||(u&&u.poster)||''}
function fmt(t){t=+t||0;return Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0')}
function render(){
  window.setV=(b,src)=>{const v=document.getElementById('pv'); const t=v.currentTime; v.src=src; v.currentTime=t; v.play&&v.play().catch(()=>{});
    b.parentNode.querySelectorAll('.chip').forEach(x=>x.classList.remove('on')); b.classList.add('on');};
  const app=document.getElementById('app'); const tabs=document.getElementById('tabs');
  tabs.innerHTML=EPS.map(e=>`<button class="tab ${e===cur?'on':''}" onclick="pick('${e.ep}')">${e.ep}</button>`).join('');
  if(!cur){app.innerHTML='<p class="meta">아직 보여 줄 회차가 없습니다. 剧集/EP00N/에 샷 표와 생성물이 생기면 여기에 나타납니다.</p>';return}
  const S=cur.shots, st=S.map(shotState);
  const cnt={ok:st.filter(x=>x.c==='ok').length,fix:st.filter(x=>x.c==='fix').length,redo:st.filter(x=>x.c==='redo').length};
  let h='';
  const P=cur.preview;
  if(P){const total=+P.seconds||S.reduce((a,s)=>a+(+s.dur||0),0);
    h+=`<section class="hero"><div class="player"><video id="pv" controls preload="metadata" src="${P.src}" ${P.poster?`poster="${P.poster}"`:''}></video></div>
    <div class="hinfo"><h2>${cur.ep} 미리보기</h2><div class="meta">${esc(P.name)} · ${P.seconds??'?'}초 · 샷 ${S.length} · 내레이션 ${P.tempo??'?'}배 · ${esc(P.time)}<br>보기용 드래프트, 자막·여는/끝 카드·효과음 층은 편집 단계에서</div>
    <div class="stats"><div class="stat ok"><b>${cnt.ok}</b><span>통과</span></div><div class="stat fix"><b>${cnt.fix}</b><span>편집</span></div><div class="stat redo"><b>${cnt.redo}</b><span>다시·영상 없음</span></div><div class="stat"><b>${fmt(total)}</b><span>길이</span></div></div>
    ${(P.alts&&P.alts.length)?`<div class="vers"><button class="chip on" onclick="setV(this,'${P.src}')">기본 판</button>${P.alts.map(a=>`<button class="chip" onclick="setV(this,'${a.src}')">${esc(a.label)}</button>`).join('')}</div>`:''}
    ${P.zip?`<a class="dl" href="${P.zip.src}" download>⬇ 클립 내려받기 <span style="font-weight:500;opacity:.75">zip · ${P.zip.mb}MB</span></a>`:''}
    ${(P.alts||[]).filter(a=>a.zip).map(a=>`<a class="dl" href="${a.zip.src}" download>⬇ ${esc(a.label)} 클립 <span style="font-weight:500;opacity:.75">zip · ${a.zip.mb}MB</span></a>`).join('')}
    <div class="bar" id="bar">${S.map((s,i)=>`<i title="#${s.num} ${esc(s.code)}" style="flex:${Math.max(+s.dur||1,.3)};background:var(--${st[i].c==='none'?'faint':st[i].c})" data-i="${i}" onclick="seek(${i})"></i>`).join('')}</div>
    <div class="strip" id="strip">${S.map((s,i)=>`<button data-i="${i}" onclick="seek(${i})" title="${esc(s.narration)}"><img loading="lazy" src="${thumbOf(s)}"><span class="n">${s.num}</span><span class="dot ${st[i].c}"></span></button>`).join('')}</div>
    <div class="now" id="now"></div></div></section>`}
  h+=`<div class="filters"><button class="chip ${filter==='all'?'on':''}" onclick="setF('all')">전체<em>${S.length}</em></button>
  <button class="chip ${filter==='todo'?'on':''}" onclick="setF('todo')">확인 필요<em>${cnt.fix+cnt.redo}</em></button>
  <button class="chip ${filter==='ok'?'on':''}" onclick="setF('ok')">통과<em>${cnt.ok}</em></button></div><div class="list">`;
  if(!S.length&&cur.outline.length) h+='<ol class="outline">'+cur.outline.map(o=>`<li><b>${esc(o.title)}</b><br>${esc(o.vo.join(' '))}</li>`).join('')+'</ol>';
  S.forEach((s,i)=>{const t=st[i]; if(filter==='ok'&&t.c!=='ok')return; if(filter==='todo'&&!(t.c==='fix'||t.c==='redo'))return;
    const ch=s.images.find(x=>x.chosen); const used=s.videos.find(v=>v.used);
    const all=[...s.images,...s.videos]; const key=(it)=>all.indexOf(it);
    const notes=[used&&used.note?`<b>영상</b> ${esc(used.note)}`:'',ch&&ch.note?`<b>키프레임</b> ${esc(ch.note)}`:''].filter(Boolean).join('<br>');
    h+=`<article class="shot" id="s${s.num}"><div class="sh"><span class="num">#${s.num}</span>${s.code?`<span class="code">${esc(s.code)}</span>`:''}
      <span class="meta">${s.dur?(+s.dur).toFixed(1)+'초':''}</span><span class="v ${t.c}">${esc(t.v)}</span>
      ${P&&s.start!=null?`<button class="ghost go" onclick="seek(${i},true)">▶ 미리보기에서</button>`:''}</div>
      <div class="nar2 ${s.narration?'':'empty'}">${s.narration?esc(s.narration):(esc(s.name)||'말 없음')}</div>
      <div class="main"><div class="slot"><div class="lbl">키프레임 ${ch?`<span class="dot ${vclass(ch.verdict)}" style="display:inline-block;width:7px;height:7px;border-radius:50%"></span> ${esc(ch.variant)}`:''}</div>
        ${ch?`<button class="media" onclick="openLB(${i},${key(ch)})"><img loading="lazy" src="${ch.thumb||ch.src}"><span class="tag">${esc(ch.verdict||'미검수')}</span></button>`:'<div class="empty-slot">고른 키프레임 없음</div>'}</div>
       <div class="slot"><div class="lbl">영상 ${used?`<span class="dot ${vclass(used.verdict)}" style="display:inline-block;width:7px;height:7px;border-radius:50%"></span> ${esc(used.variant)} · 미리보기에 사용`:''}</div>
        ${used?`<button class="media" onclick="openLB(${i},${key(used)})"><img loading="lazy" src="${used.poster||''}"><span class="play"><span>▶</span></span><span class="tag">${esc(used.verdict||'미검수')}</span></button>`:`<div class="empty-slot">${s.still?'영상 없음, 미리보기는 정지 그림':'영상 없음'}</div>`}</div></div>
      ${notes?`<div class="note">${notes}</div>`:''}
      ${all.length>1?`<div class="hist"><h4>기록 · 키프레임 ${s.images.length} · 영상 ${s.videos.length}</h4><div class="thumbs">${all.map((it,k)=>`<button class="th ${(it.chosen||it.used)?'cur':''}" onclick="openLB(${i},${k})" title="${esc(it.variant)} ${esc(it.verdict)} ${esc(it.note)}"><img loading="lazy" src="${it.kind==='image'?(it.thumb||it.src):(it.poster||'')}"><span class="dot ${vclass(it.verdict)}"></span><span class="k">${it.kind==='video'?'▶ ':''}${esc(it.variant)}</span></button>`).join('')}</div></div>`:''}
      ${s.jobs.filter(j=>j.status!=='succeeded').map(j=>`<div class="job">⚠ ${esc(j.name)} · ${esc(j.status)} ${esc(j.error)}</div>`).join('')}
      ${(s.kprompt||s.mprompt)?`<details class="pr"><summary>프롬프트</summary>${s.kprompt?`<p><b>키프레임</b> ${esc(s.kprompt)}</p>`:''}${s.mprompt?`<p><b>영상</b> ${esc(s.mprompt)}</p>`:''}</details>`:''}
    </article>`});
  app.innerHTML=h+'</div>';
  const pv=document.getElementById('pv'); if(pv) pv.addEventListener('timeupdate',()=>mark(pv.currentTime));
}
function pick(ep){cur=EPS.find(e=>e.ep===ep);try{localStorage.setItem('board-ep',ep)}catch(e){};render()}
function setF(f){filter=f;try{localStorage.setItem('board-filter',f)}catch(e){};render()}
function seek(i,top){const s=cur.shots[i],pv=document.getElementById('pv');if(!pv||s.start==null)return;pv.currentTime=s.start+0.02;pv.play();
  if(top) window.scrollTo({top:0,behavior:'smooth'}); else {const el=document.getElementById('s'+s.num); if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),1200)}}}
let lastMark=-1;
function mark(t){const S=cur.shots;let k=-1;for(let i=0;i<S.length;i++){if(S[i].start!=null&&t>=S[i].start)k=i}
  if(k===lastMark)return;lastMark=k;
  document.querySelectorAll('#strip button,#bar i').forEach(b=>b.classList.toggle('cur',+b.dataset.i===k));
  const sb=document.querySelector(`#strip button[data-i="${k}"]`); if(sb) sb.scrollIntoView({block:'nearest',inline:'center'});
  const s=S[k]; const now=document.getElementById('now'); if(now&&s) now.innerHTML=`<b>#${s.num} ${esc(s.code)}</b> ${esc(s.narration||s.name||'말 없음')}`}
function openLB(i,k){const s=cur.shots[i];lbItems=[...s.images,...s.videos].map(x=>({...x,shot:s}));lbIdx=k;drawLB();document.getElementById('lb').classList.add('open');
  const pv=document.getElementById('pv'); if(pv) pv.pause()}
function closeLB(){document.getElementById('lb').classList.remove('open');document.getElementById('lbStage').innerHTML=''}
function stepLB(d){if(!lbItems.length)return;lbIdx=(lbIdx+d+lbItems.length)%lbItems.length;drawLB()}
function drawLB(){const it=lbItems[lbIdx],s=it.shot;
  document.getElementById('lbStage').innerHTML=it.kind==='video'?`<video src="${it.src}" ${it.poster?`poster="${it.poster}"`:''} controls autoplay loop playsinline></video>`:`<img src="${it.src}">`;
  document.getElementById('lbSide').innerHTML=`<h3>#${s.num} ${esc(s.code)} · ${it.kind==='video'?'영상':'키프레임'} ${esc(it.variant)}</h3>
   <div class="meta">${esc(s.narration||s.name||'')}</div>
   <dl class="kv"><dt>판정</dt><dd><span class="v ${vclass(it.verdict)}">${esc(it.verdict||'미검수')}</span> ${it.chosen?'· 고른 키프레임':''}${it.used?'· 미리보기에 사용':''}</dd>
   ${it.note?`<dt>메모</dt><dd>${esc(it.note)}</dd>`:''}${it.rules&&it.rules.length?`<dt>규칙</dt><dd>${esc(it.rules.join(', '))}</dd>`:''}
   ${it.by?`<dt>검수</dt><dd>${esc(it.by)}</dd>`:''}<dt>파일</dt><dd>${esc(it.name)}</dd><dt>시각</dt><dd>${esc(it.time)}</dd></dl>
   ${it.strip?`<div class="meta" style="margin-top:12px">머리 확인 프레임(0·25·50·75·끝)</div><img class="stripimg" src="${it.strip}">`:''}
   <div class="thumbs">${lbItems.map((x,k)=>`<button class="th ${k===lbIdx?'cur':''}" onclick="lbIdx=${k};drawLB()"><img src="${x.kind==='image'?(x.thumb||x.src):(x.poster||'')}"><span class="dot ${vclass(x.verdict)}"></span><span class="k">${x.kind==='video'?'▶ ':''}${esc(x.variant)}</span></button>`).join('')}</div>
   <p class="meta" style="margin-top:14px">← → 넘기기 · Esc 닫기</p>`}
document.addEventListener('keydown',e=>{if(!document.getElementById('lb').classList.contains('open'))return;
  if(e.key==='Escape')closeLB();if(e.key==='ArrowRight')stepLB(1);if(e.key==='ArrowLeft')stepLB(-1)});
document.getElementById('lb').addEventListener('click',e=>{if(e.target.id==='lb'||e.target.id==='lbStage')closeLB()});
let saved=null;try{saved=localStorage.getItem('board-ep');filter=localStorage.getItem('board-filter')||'all'}catch(e){}
cur=EPS.find(e=>e.ep===saved)||EPS[EPS.length-1]||null;render();
</script></body></html>"""


def render(project: Path, board: Path, episodes: list[dict], title: str) -> str:
    data = [episode_data(project, board, e) for e in episodes]
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    try:  # 피드백·규칙 페이지를 함께 갱신
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_feedback", Path(__file__).with_name("build_feedback.py"))
        bf = importlib.util.module_from_spec(spec); spec.loader.exec_module(bf)
        argv, sys.argv = sys.argv, [sys.argv[0], str(project)]
        try:
            bf.main()
        finally:
            sys.argv = argv
    except Exception as exc:  # 보드는 그래도 만든다
        print(f"feedback.html 실패: {exc}", file=sys.stderr)
    feedback = '<a class="ghost" href="feedback.html">피드백·규칙</a>' if (board / "feedback.html").exists() else ""
    return (PAGE.replace("__TITLE__", esc(title)).replace("__BUILT__", dt.datetime.now().strftime("%m-%d %H:%M"))
            .replace("__FEEDBACK__", feedback).replace("__DATA__", blob))



def build(project: Path, episode: str | None = None) -> Path:
    project = project.resolve()
    board = project / ".board"
    board.mkdir(exist_ok=True)
    prod = production_index(project)
    ep_root = project / "剧集"
    ep_dirs = sorted(p for p in ep_root.glob("EP[0-9][0-9][0-9]") if p.is_dir()) if ep_root.is_dir() else []
    if episode:
        ep_dirs = [p for p in ep_dirs if p.name == episode]
    episodes = [collect_episode(project, board, d, prod) for d in ep_dirs]
    title = "제작"
    try:
        title = json.loads((project / "short-drama.json").read_text(encoding="utf-8")).get("title") or title
    except (OSError, ValueError):
        pass
    out = board / "index.html"
    out.write_text(render(project, board, episodes, title), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="drama-skills 제작 보드(읽기 전용)")
    parser.add_argument("project")
    parser.add_argument("--episode")
    args = parser.parse_args()
    print(build(Path(args.project), args.episode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
