#!/usr/bin/env python3
"""分镜.md의 SHOT 중 시작 프레임이 아직 없는(PLAN-SHOT-START) 샷마다 drama-skills 이미지 job JSON을 만든다.

python3 <TOOLS>/pipeline/make_image_jobs.py <project> <EP> <out_dir> [--adapter codex-image] [--candidate c01] [--only SHOT-EP001-014,...]
job: source=剧集/<EP>/分镜.md, source_entry=SHOT-…, prompt=冻结关键帧提示词 인용문, REF 슬롯 그대로(PLAN 제외),
output=剧集/<EP>/制作成果/images/<SHOT>_<cNN>.png
"""
import argparse, json, re, sys
from pathlib import Path

REF_RE = re.compile(r"(?P<slot>(?:REF|PLAN)-[^（\s]+)（顺序：(?P<order>\d+)）·\s*(?P<path>[^《]+?)《(?P<label>[^》]*)》（用途：(?P<use>[^；）]+)(?:；控制：(?P<may>[^；）]*))?(?:；不得控制：(?P<mustnot>[^）]*))?）")


def split_list(value):
    return [v.strip() for v in re.split(r"[、,，]", value or "") if v.strip()]


def shots(text):
    out, cur, sub = {}, None, None
    for line in text.splitlines():
        m = re.match(r"^## (SHOT-EP\d{3}-\d{3}[A-Za-z]?)\b", line)
        if m:
            cur = {"id": m.group(1), "refs": [], "quote": [], "plan_start": False}
            out[cur["id"]] = cur; sub = None; continue
        if cur is None:
            continue
        if line.startswith("## "):
            cur = None; continue
        if line.startswith("### "):
            sub = line[4:].strip(); continue
        s = line.strip()
        if sub == "冻结关键帧提示词" and s.startswith(">"):
            cur["quote"].append(re.sub(r"^>\s?", "", s))
        elif s.startswith("- ") and "参考图" in s.split("：", 1)[0]:
            for m in REF_RE.finditer(s):
                d = m.groupdict()
                if d["slot"].startswith("PLAN-") and d["use"].strip() == "起始帧":
                    cur["plan_start"] = True
                elif d["slot"].startswith("REF-"):
                    cur["refs"].append(d)
    return out


def _production_tool():
    import importlib.util
    here = Path(__file__).resolve().parent.parent.parent
    path = here / "skills/short-drama-produce/scripts/production_tool.py"
    spec = importlib.util.spec_from_file_location("production_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("out_dir")
    ap.add_argument("--adapter", default="codex-image"); ap.add_argument("--candidate", default="c01")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    pt = _production_tool()
    project = Path(a.project)
    src = f"剧集/{a.ep}/分镜.md"
    document = (project / src).read_text(encoding="utf-8")
    _, heading, field_name = pt.CREATOR_SOURCE_ENTRIES["分镜.md"]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    made, skipped = [], []
    for sid, info in shots(document).items():
        if (only and sid not in only):
            continue
        if not info["plan_start"]:
            skipped.append(sid)
            continue
        section = pt._markdown_section(document, sid)
        prompt = pt._copyable_prompt(section, heading)
        declared = pt._markdown_reference_bindings(section, field_name=field_name, creator_supplied_ok=True)
        bindings = [{**b, "role": "reference_image"} for b in declared]
        job = {"schema_version": "1.0", "job_id": f"{sid}-IMG-{a.candidate}", "modality": "image", "adapter": a.adapter,
               "prompt": prompt, "source": src, "source_entry": sid, "reference_bindings": bindings,
               "outputs": [f"剧集/{a.ep}/制作成果/images/{sid}_{a.candidate}.png"],
               "parameters": {"ratio": "9:16", "prompt_language": "en"}, "overwrite": False}
        path = out / f"{sid}_{a.candidate}.json"
        path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        made.append(sid)
    print(json.dumps({"jobs": len(made), "skipped_has_start_frame": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
