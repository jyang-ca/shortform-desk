#!/usr/bin/env python3
"""图片提示词.md(IMG-) / 视频提示词.md(MOTION-) 항목에서 drama-skills job JSON을 만든다(drama-skills 자체 파서 사용).

python3 <TOOLS>/pipeline/make_jobs.py <project> <EP> image <out_dir> [--adapter codex-image] [--only IMG-…,…]
python3 <TOOLS>/pipeline/make_jobs.py <project> <EP> video <out_dir> [--adapter sume-video] [--only MOTION-…,…] [--resolution 360p] [--take t01]
"""
import argparse, importlib.util, json, math, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("pt", ROOT / "skills/short-drama-produce/scripts/production_tool.py")
pt = importlib.util.module_from_spec(spec); spec.loader.exec_module(pt)
ROLE = {"起始帧": "first_frame", "结束帧": "last_frame"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("kind", choices=["image", "video"]); ap.add_argument("out")
    ap.add_argument("--adapter"); ap.add_argument("--only", default=""); ap.add_argument("--resolution", default="360p")
    ap.add_argument("--take", default="t01"); ap.add_argument("--subdir", default="", help="video/<subdir>/ 아래로 (시험 테이크)")
    a = ap.parse_args()
    project, out = Path(a.project), Path(a.out); out.mkdir(parents=True, exist_ok=True)
    doc = "图片提示词.md" if a.kind == "image" else "视频提示词.md"
    src = f"剧集/{a.ep}/{doc}"; text = (project / src).read_text(encoding="utf-8")
    prefix, heading, field = pt.CREATOR_SOURCE_ENTRIES[doc]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    made = []
    for m in re.finditer(rf"^## ({prefix}[A-Za-z0-9-]+)", text, re.M):
        eid = m.group(1)
        if only and eid not in only:
            continue
        section = pt._markdown_section(text, eid)
        prompt = pt._copyable_prompt(section, heading)
        declared = pt._markdown_reference_bindings(section, field_name=field, creator_supplied_ok=False)
        if a.kind == "image":
            target = re.search(r"^- 产出：(\S+)", section, re.M).group(1)
            bindings = [{**b, "role": "reference_image"} for b in declared]
            params = {"ratio": "9:16", "prompt_language": "en"}
            adapter = a.adapter or "codex-image"
        else:
            uses = re.findall(r"（顺序：\d+）·[^《]+《[^》]*》（用途：([^；）]+)", re.search(rf"^- {field}：(.*)$", section, re.M).group(1))
            bindings = [{**b, "role": ROLE.get(u.strip(), "reference_image")} for b, u in zip(declared, uses)]
            seconds = float(re.search(r"^- 时长：([\d.]+)", section, re.M).group(1))
            hold = "首尾同帧" in (re.search(r"^- 状态链：(.*)$", section, re.M) or [None, ""])[1]
            params = {"duration": max(3, math.ceil(seconds + 0.3)), "resolution": a.resolution, "ratio": "9:16",
                      "hold_last_frame": hold, "generate_audio": True, "prompt_language": "en"}
            num = re.search(r"-(\d{3}[A-Z]?)(?:-[A-Z0-9]+)?$", eid).group(1)
            target = f"剧集/{a.ep}/制作成果/video/" + (a.subdir + "/" if a.subdir else "") + f"MOTION-{a.ep}-{num}_{a.take}.mp4"
            adapter = a.adapter or "sume-video"
        job = {"schema_version": "1.0", "job_id": f"{eid}-{a.take if a.kind == 'video' else Path(target).stem.split('_')[-1]}",
               "modality": a.kind, "adapter": adapter, "prompt": prompt, "source": src, "source_entry": eid,
               "reference_bindings": bindings, "outputs": [target], "parameters": params, "overwrite": False}
        (out / f"{job['job_id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        made.append(job["job_id"])
    print(json.dumps({"jobs": len(made)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
