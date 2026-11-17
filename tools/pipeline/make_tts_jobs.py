#!/usr/bin/env python3
"""샷마다 내레이션 한 줄씩 drama-skills TTS job JSON을 만든다(목소리·모델은 시리즈/설정.json production).

python3 <TOOLS>/pipeline/make_tts_jobs.py <project> EP001 <out_dir> [--take vo01] [--only 003,007]

- 줄: 분镜.md 声音 줄의 VO「…」(shotlist.py와 같은 규칙). 줄 그대로 말한다(고치지 않는다 — R-NARR-07)
- 억양 잇기: 앞·뒤 샷 줄을 previous_text / next_text로 넘긴다(말하지 않음)
- 출력 경로: 剧集/<EP>/制作成果/tts/SHOT-<EP>-<nnn>_<take>.mp3 (assemble_preview.py는 _vo01을 쓴다 — 다른 테이크를 고르면 이름을 바꾸거나 vo01로 복사)
- 숫자 읽기 등 TTS용 고친 줄은 剧本.md를 고치지 말고 --text-map JSON {"003": "TTS용 줄"}로 넘긴다
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from pipeline import shotlist  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep"); ap.add_argument("out_dir")
    ap.add_argument("--take", default="vo01"); ap.add_argument("--only", default=""); ap.add_argument("--text-map")
    a = ap.parse_args()
    P = Path(a.project).resolve(); S = common.load_series(P); pr = S["production"]
    if not pr.get("voice_id"):
        raise SystemExit("시리즈/설정.json production.voice_id가 비어 있다 — series-setup에서 목소리를 정한다")
    tmap = json.loads(Path(a.text_map).read_text(encoding="utf-8")) if a.text_map else {}
    rows = [r for r in shotlist.rows_from_storyboard(P, a.ep) if r["대사"]]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    made = []
    for i, r in enumerate(rows):
        n = r["샷번호"]
        if only and n not in only:
            continue
        text = tmap.get(n, r["대사"])
        params = {"voice_id": pr["voice_id"], "model_id": pr.get("tts_model", "eleven_v4"), **pr.get("tts_settings", {})}
        if i > 0:
            params["previous_text"] = tmap.get(rows[i - 1]["샷번호"], rows[i - 1]["대사"])
        if i + 1 < len(rows):
            params["next_text"] = tmap.get(rows[i + 1]["샷번호"], rows[i + 1]["대사"])
        sid = f"SHOT-{a.ep}-{n}"
        job = {"schema_version": "1.0", "job_id": f"{sid}-TTS-{a.take}", "modality": "tts", "adapter": pr.get("tts_adapter", "elevenlabs-tts"),
               "prompt": text, "source": f"剧集/{a.ep}/剧本.md", "reference_bindings": [],
               "outputs": [f"剧集/{a.ep}/制作成果/tts/{sid}_{a.take}.mp3"], "parameters": params, "overwrite": False}
        (out / f"{sid}_{a.take}.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        made.append(n)
    print(json.dumps({"jobs": len(made), "shots": made}, ensure_ascii=False))


if __name__ == "__main__":
    main()
