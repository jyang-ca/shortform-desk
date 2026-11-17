#!/usr/bin/env python3
"""프롬프트 사전 검사(문장 린트) — 크레딧 쓰기 전에, 규칙집 '예방' 규칙 중 글로 잡을 수 있는 것.

python3 <TOOLS>/qa/prompt_lint.py <project> EP001 [--only IMG|MOTION] [--ids SHOT-EP001-012,...]
python3 <TOOLS>/qa/prompt_lint.py <project> --text "prompt ..." --kind video      # 한 줄 시험

읽는 것: 剧集/<EP>/图片提示词.md(IMG-…), 视频提示词.md(MOTION-…), 分镜.md(冻结关键帧提示词),
        시리즈/설정.json(금지 낱말·인물 라벨·인물 문장·안전 설정), 검수/현재-결정표.json, 검수/막힌-문장.jsonl
출력: stdout JSON 요약 + 审查/<EP>_문장린트.md
판정: fail(생성하지 않는다) / warn(사전 검사 에이전트가 본다)
"""
import argparse, hashlib, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

NEGATED = r"\bno (?:\w+ )?body(?: details)?\b|\bno anatomy\b"
CAMERA_TERMS = r"over the (?:right |left )?shoulder|cropped at the waist|from the waist up"
HEAD_LOCK = r"back of the head|never turns|head stays|seen from above|far away|tiny|head(?:s)? (?:is |are )?out of frame|face stays blank|stays blank"
HAND_ONLY = r"\bonly (?:the |her |his |their |one )?(?:hands?|feet|foot)\b|\bhands? only\b|\b(?:head|body)[^.]{0,40}out of frame"


def words(lst):
    return r"\b(" + "|".join(lst) + r")\b" if lst else None


def entries(doc: Path, prefix: str):
    """'## IMG-…' / '## MOTION-…' / '## SHOT-…' 블록에서 '> ' 인용 줄(복사용 프롬프트)과 참조 줄을 뽑는다."""
    if not doc.exists():
        return []
    out = []
    for block in re.split(r"(?m)^## ", doc.read_text(encoding="utf-8"))[1:]:
        head = block.splitlines()[0]
        if not head.startswith(prefix):
            continue
        prompt = " ".join(l[2:] for l in block.splitlines() if l.startswith("> "))
        refs = " ".join(l for l in block.splitlines() if re.match(r"- (参考|输入参考图|参考图)：", l))
        shot = re.search(r"SHOT-[A-Z0-9]+-\d+[A-Z]?", block)
        out.append({"id": head.split(" ")[0], "shot": shot.group(0) if shot else None, "prompt": prompt, "refs": refs})
    return out


def lint(e, kind, S, decisions, blocked):
    p, issues = e["prompt"], []
    low = p.lower()
    safety, chars = S["safety"], [c for c in S.get("characters", []) if c.get("name") or c.get("image_sentence")]

    def add(level, rule, msg):
        issues.append({"level": level, "rule": rule, "msg": msg})

    if not p:
        add("fail", "-", "복사용 프롬프트('> ' 줄) 없음")
        return issues
    if hashlib.sha256(p.strip().encode()).hexdigest() in blocked:
        add("fail", "R-SAFE-04", "안전 검사에 막혔던 문장과 똑같다 — 구도·지칭부터 바꾼다")
    fw = words(safety.get("forbidden_words"))
    if fw:
        for m in re.finditer(fw, low):
            add("fail", "R-SAFE-01", f"금지 낱말 '{m.group(0)}'")
    present = re.sub(r"\bno (?:figure|person|people|one|other people)\b[^.]*", " ", low)  # '인물 없음' 문장은 인물로 세지 않는다
    names = [re.escape(w.lower()) for c in chars for w in c.get("prompt_words", []) if w]  # 인물을 가리키는 영어 낱말(설정)
    figw = words((safety.get("figure_words") or ["figure"]) + names)
    figure = bool(re.search(figw, present)) or bool(re.search(r"\bhands?\b", present))
    hand_only = bool(re.search(HAND_ONLY, low))
    faceless = any(c.get("faceless") for c in chars)
    if kind == "image":
        labels = [c["ref_label"] for c in chars if c.get("ref_label")]
        if figure and labels and not any(l.rstrip("》") in e["refs"] for l in labels):
            add("fail", "R-ID-02", f"인물이 나오는데 인물 참조 라벨({', '.join(labels)})이 참조 줄에 없다")
        if figure and not hand_only:
            for c in chars:
                sent = (c.get("image_sentence") or "").strip()
                if sent and c.get("ref_label", "").rstrip("》") in e["refs"] and sent[:60].lower() not in low:
                    add("warn", "R-ID-02", f"{c['id']} 인물 문장(설정 image_sentence)이 그대로 들어 있지 않다")
        if safety.get("require_no_text_line", True) and not re.search(r"no (readable )?text", low):
            add("warn", "R-TEXT-01", f"'{safety.get('no_text_line') or 'No readable text, no logos, no watermark.'}' 줄이 없다")
    else:
        vids = [c for c in chars if (c.get("video_sentence") or "").strip()]
        if figure and not hand_only and vids and not any(c["video_sentence"].strip()[:60].lower() in low for c in vids):
            add("fail", "R-ID-02", "인물이 나오는데 설정의 영상용 인물 문장(characters[].video_sentence)이 없다")
        if hand_only:
            hs = [c["hands_sentence"] for c in chars if (c.get("hands_sentence") or "").strip()]
            if hs and not any(h.strip()[:40].lower() in low for h in hs):
                add("warn", "R-ID-02", "손·발만 나오는 샷인데 설정의 손 문장(hands_sentence)이 없다")
        if safety.get("video_body_word_check"):
            scrub = re.sub(NEGATED, " ", low)
            camera = sorted({m.group(0) for m in re.finditer(CAMERA_TERMS, scrub)})
            bw_re = words(safety.get("body_words"))
            bw = sorted({m.group(0) for m in re.finditer(bw_re, re.sub(CAMERA_TERMS, " ", scrub))}) if bw_re else []
            if bw:
                add("fail", "R-SAFE-04", f"영상 문장에 몸·성별 낱말 {bw} — 설정 안전 규칙대로 'the figure'·'the head' 같은 말로")
            if camera:
                add("warn", "R-SAFE-04", f"카메라 용어에 몸 낱말 {camera} — 'from behind and above'·'close behind the head' 같은 말이 더 안전")
        if faceless and figure and not hand_only and not re.search(HEAD_LOCK, low):
            add("warn", "R-MOV-01", "얼굴 없는 인물인데 머리·얼굴을 잠그는 줄(뒤통수 유지·얼굴은 비어 있음 등)이 없다 — 이목구비가 생길 위험")
    for d in decisions:
        if d.get("applies", "all") not in ("all", kind) or not d.get("trigger"):
            continue
        if not re.search(d["trigger"], p, re.I):
            continue
        if d.get("forbid"):
            affirm = re.sub(r"\b(?:no|nothing|not|never|without)\b[^.;:]*", " ", p, flags=re.I)  # 부정문은 빼고 본다
            m = re.search(d["forbid"], affirm, re.I)
            if m:
                add(d.get("level", "fail"), d.get("rule", "R-PROP-03"), f"{d['id']}: '{m.group(0)}' — {d.get('what', '')}")
        if d.get("require") and not re.search(d["require"], p, re.I):
            add(d.get("level", "warn"), d.get("rule", "R-PROP-03"), f"{d['id']}: '{d['require']}' 없음 — {d.get('what', '')}")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("ep", nargs="?")
    ap.add_argument("--only", choices=["IMG", "MOTION", "SHOT"]); ap.add_argument("--ids", default="")
    ap.add_argument("--text"); ap.add_argument("--kind", choices=["image", "video"], default="image")
    a = ap.parse_args()
    P = Path(a.project).resolve()
    S = common.load_series(P)
    dp = P / "검수" / "현재-결정표.json"
    decisions = json.loads(dp.read_text(encoding="utf-8")).get("decisions", []) if dp.exists() else []
    bl = P / "검수" / "막힌-문장.jsonl"
    blocked = {json.loads(l)["sha256"] for l in bl.read_text(encoding="utf-8").splitlines() if l.strip()} if bl.exists() else set()
    if a.text:
        labels = " ".join(c.get("ref_label", "") for c in S.get("characters", []))
        print(json.dumps(lint({"prompt": a.text, "refs": labels if a.kind == "image" else ""}, a.kind, S, decisions, blocked),
                         ensure_ascii=False, indent=1))
        return
    if not a.ep:
        ap.error("EP가 필요하다(또는 --text)")
    E = P / "剧集" / a.ep
    items = []
    if a.only in (None, "IMG"):
        items += [("image", e) for e in entries(E / "图片提示词.md", "IMG-")]
    if a.only in (None, "MOTION"):
        items += [("video", e) for e in entries(E / "视频提示词.md", "MOTION-")]
    if a.only in (None, "SHOT"):  # 分镜.md의 冻结关键帧提示词도 이미지 프롬프트로 본다
        items += [("image", e) for e in entries(E / "分镜.md", "SHOT-") if e["prompt"]]
    ids = {x for x in a.ids.split(",") if x}
    if ids:
        items = [(k, e) for k, e in items if e["shot"] in ids or e["id"] in ids]
    rows, fails, warns = [], 0, 0
    for kind, e in items:
        for i in lint(e, kind, S, decisions, blocked):
            rows.append((e["id"], i)); fails += i["level"] == "fail"; warns += i["level"] == "warn"
    out = P / "审查" / f"{a.ep}_문장린트.md"
    out.parent.mkdir(exist_ok=True)
    md = [f"# {a.ep} 문장 린트", "", f"- 검사 {len(items)}개 · fail {fails} · warn {warns}",
          "- 기준: 규칙집 예방 규칙, `시리즈/설정.json`, `검수/현재-결정표.json`, `검수/막힌-문장.jsonl`", "",
          "| 항목 | 판정 | 규칙 | 내용 |", "|---|---|---|---|"]
    md += [f"| {i} | {x['level']} | {x['rule']} | {x['msg']} |" for i, x in rows]
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"checked": len(items), "fail": fails, "warn": warns, "report": str(out.relative_to(P)),
                      "fail_items": sorted({i for i, x in rows if x["level"] == "fail"})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
