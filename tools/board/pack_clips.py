#!/usr/bin/env python3
"""회차 미리보기에 들어간 클립을 샷 순서대로 zip 하나로 묶는다(보드의 '클립 내려받기').

python3 <TOOLS>/board/pack_clips.py <project> EP001 [--version v06]

- 기준: 剧集/<EP>/制作成果/成片/<EP>_미리보기_<ver>.json 의 샷별 source(테이크 또는 정지 그림)
- zip 안: clips/NN_<E코드>_<원래 파일 이름>, <EP>_미리보기_<ver>.mp4, 타임라인 .json, 목록.csv(순서·샷·파일·판정·내레이션)
- 출력: 剧集/<EP>/制作成果/成片/<EP>_클립_<ver>.zip (미리보기보다 오래됐을 때만 다시 만든다)
"""
import argparse, csv, io, json, re, zipfile
from pathlib import Path


def latest_version(out_dir: Path, ep: str):
    cands = sorted(out_dir.glob(f"{ep}_미리보기_v*.json"), key=lambda p: p.stat().st_mtime)
    return re.search(r"_(v\d+[a-z]*)\.json$", cands[-1].name).group(1) if cands else None


def pack(project: Path, ep: str, version=None, force=False):
    media = project / "剧集" / ep / "制作成果"; out_dir = media / "成片"
    version = version or latest_version(out_dir, ep)
    if not version:
        return None
    tl_path = out_dir / f"{ep}_미리보기_{version}.json"; preview = out_dir / f"{ep}_미리보기_{version}.mp4"
    zpath = out_dir / f"{ep}_클립_{version}.zip"
    newest = max(p.stat().st_mtime for p in (tl_path, preview) if p.exists())
    if zpath.exists() and zpath.stat().st_mtime >= newest and not force:
        return zpath
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    verdicts = json.loads((media / "_검수.json").read_text(encoding="utf-8")) if (media / "_검수.json").exists() else {}
    rows = [["순서", "샷", "코드", "zip 안 파일", "원본(프로젝트 기준)", "종류", "판정", "내레이션"]]
    tmp = zpath.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w") as z:
        for s in tl["shots"]:
            src = str(s["source"])
            kind = "정지 그림" if src.startswith("정지") else "영상"
            rel = re.sub(r"^정지 화면 ", "", src).split(" (")[0].strip()
            f = project / rel
            if not f.exists():
                continue
            name = f"clips/{s['shot']}_{s['code']}_{f.name}"
            z.write(f, name, compress_type=zipfile.ZIP_STORED)  # 영상·그림은 이미 압축돼 있다
            v = verdicts.get(rel, {}).get("verdict", "")
            rows.append([s["shot"], f"SHOT-{ep}-{s['shot']}", s["code"], name, rel, kind, v, s.get("narration") or ""])
        if preview.exists():
            z.write(preview, preview.name, compress_type=zipfile.ZIP_STORED)
        z.write(tl_path, tl_path.name, compress_type=zipfile.ZIP_DEFLATED)
        buf = io.StringIO(); csv.writer(buf).writerows(rows)
        z.writestr("목록.csv", "﻿" + buf.getvalue(), compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(zpath)
    return zpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("--version"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    z = pack(Path(a.project).resolve(), a.ep, a.version, a.force)
    if z:
        with zipfile.ZipFile(z) as f:
            n = sum(1 for i in f.namelist() if i.startswith("clips/"))
        print(json.dumps({"zip": str(z), "clips": n, "mb": round(z.stat().st_size / 1e6, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
