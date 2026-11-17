#!/usr/bin/env python3
"""피드백·규칙 보기 페이지(보기 전용) — 보드 맨 위 '피드백·규칙' 탭.

python3 <TOOLS>/board/build_feedback.py <project>

읽는 것: 검수/피드백-기록.jsonl(데스크가 받은 지시·피드백과 분류), 검수/규칙집_v*.md 최신 판(R·S 카드),
        검수/지표.jsonl(전달마다 한 줄), 시리즈/설정.json(제목)
출력: <project>/.board/feedback.html
화면: 숫자 요약(지적 수·놓침·새 규칙·회차별 추이) → 피드백 목록(원문·지적·분류·규칙) → 규칙 카드(이 규칙에 걸린 지적 수·놓침 수) → 전달 지표
"""
from __future__ import annotations

import html, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

CLS = {"놓침": "miss", "새 규칙": "new", "이번만": "once", "취향 답": "taste", "지시": "order"}


def jl(p: Path):
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if l:
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                pass
    return out


def rules(book: Path | None):
    if not book:
        return []
    out = []
    for m in re.finditer(r"^### ((?:R|S)-[A-Z]+-\d+)\s+(.*)$", book.read_text(encoding="utf-8"), re.M):
        out.append({"id": m.group(1), "title": m.group(2).strip()})
    return out


def main():
    P = Path(sys.argv[1]).resolve()
    try:
        title = common.load_series(P).get("title") or P.name
    except SystemExit:
        title = P.name
    fb = jl(P / "검수" / "피드백-기록.jsonl")
    metrics = jl(P / "검수" / "지표.jsonl")
    book = common.latest_rulebook(P)
    R = rules(book)
    esc = lambda v: html.escape(str(v if v is not None else ""), quote=True)

    hits, miss = Counter(), Counter()
    by_ep = defaultdict(Counter)
    for f in fb:
        for pt in f.get("points", []):
            c = pt.get("class", "")
            if f.get("kind") == "피드백":
                by_ep[f.get("ep") or "-"][c] += 1
            for rid in re.findall(r"[RS]-[A-Z]+-\d+", pt.get("rule") or ""):
                hits[rid] += 1
                if c == "놓침":
                    miss[rid] += 1
    pts = [pt for f in fb if f.get("kind") == "피드백" for pt in f.get("points", [])]
    cnt = Counter(pt.get("class", "") for pt in pts)
    s_rules = sum(1 for r in R if r["id"].startswith("S-"))

    eps = "".join(
        f"<tr><td>{esc(ep)}</td><td>{sum(c.values())}</td><td>{c['놓침']}</td><td>{c['새 규칙']}</td><td>{c['이번만']}</td></tr>"
        for ep, c in sorted(by_ep.items()))
    items = []
    for f in reversed(fb):
        chips = "".join(
            f"<li><span class='chip {CLS.get(pt.get('class', ''), '')}'>{esc(pt.get('class', ''))}</span> {esc(pt.get('text', ''))}"
            + "".join(f" <a class='rid' href='#{r}'>{r}</a>" for r in re.findall(r"[RS]-[A-Z]+-\d+", pt.get("rule") or "")) + "</li>"
            for pt in f.get("points", []))
        tg = ", ".join(map(str, f.get("targets") or []))
        items.append(f"<article class='fb {esc(f.get('kind', ''))}'><header><b>{esc(f.get('ts', ''))}</b> · {esc(f.get('ep') or '-')}"
                     f" · {esc(f.get('kind', ''))}{(' · 샷 ' + esc(tg)) if tg else ''}</header>"
                     f"<blockquote>{esc(f.get('quote', ''))}</blockquote><ul>{chips}</ul></article>")
    cards = "".join(
        f"<tr id='{esc(r['id'])}'><td class='mono'>{esc(r['id'])}</td><td>{esc(r['title'])}</td><td>{hits[r['id']] or ''}</td>"
        f"<td class='{'bad' if miss[r['id']] else ''}'>{miss[r['id']] or ''}</td></tr>"
        for r in sorted(R, key=lambda r: (-miss[r["id"]], -hits[r["id"]], r["id"])))
    mt = "".join(
        f"<tr><td>{esc(m.get('ts'))}</td><td>{esc(m.get('ep'))}</td><td>{esc(m.get('gate'))}</td><td>{esc(m.get('delivered'))}</td>"
        f"<td>{esc(m.get('auto_fixed'))}</td><td>{esc(m.get('user_points', ''))}</td><td>{esc(m.get('missed', ''))}</td>"
        f"<td>{esc(m.get('cost_usd'))}</td><td>{esc(m.get('minutes'))}</td></tr>" for m in reversed(metrics))

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>피드백·규칙 — {esc(title)}</title><style>
:root{{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e2e2dc;--miss:#c0392b;--new:#2d6cdf;--once:#888;--taste:#8e44ad;--order:#2e7d32}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecece8;--mut:#a0a09a;--card:#1f1f1d;--line:#33332f;--miss:#ff6b5b;--new:#6ea0ff;--once:#999;--taste:#c39bd3;--order:#7bc47f}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 60px}} a{{color:var(--new)}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 10px}} .meta{{color:var(--mut);font-size:13px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}} .stat b{{display:block;font-size:24px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--mut);font-weight:600}}
.wrap{{overflow-x:auto}} .mono{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap}} td.bad{{color:var(--miss);font-weight:700}}
.fb{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}}
.fb header{{color:var(--mut);font-size:13px}} blockquote{{margin:8px 0;padding-left:10px;border-left:3px solid var(--line);white-space:pre-wrap}}
.fb ul{{margin:6px 0 0;padding-left:18px}} .chip{{display:inline-block;font-size:12px;padding:1px 7px;border-radius:9px;color:#fff;background:var(--once)}}
.chip.miss{{background:var(--miss)}} .chip.new{{background:var(--new)}} .chip.taste{{background:var(--taste)}} .chip.order{{background:var(--order)}}
.rid{{font-family:ui-monospace,Menlo,monospace;font-size:12px}} nav a{{margin-right:12px}}
</style></head><body><main>
<nav><a href="index.html">← 제작 보드</a></nav>
<h1>피드백·규칙 — {esc(title)}</h1>
<div class="meta">규칙집: {esc(book.name if book else '없음')} · 피드백 기록 {len(fb)}줄 · 다시 만들 때마다 갱신(보기 전용)</div>
<div class="stats">
<div class="stat"><b>{len(pts)}</b>사용자 지적</div>
<div class="stat"><b style="color:var(--miss)">{cnt['놓침']}</b>놓침(규칙이 있었는데 검수가 통과시킴)</div>
<div class="stat"><b style="color:var(--new)">{cnt['새 규칙']}</b>새 규칙으로 올림</div>
<div class="stat"><b>{cnt['이번만']}</b>이번만</div>
<div class="stat"><b>{len(R)}</b>규칙 카드(시리즈 S {s_rules})</div>
</div>
<h2>회차별 지적</h2><div class="wrap"><table><tr><th>회차</th><th>지적</th><th>놓침</th><th>새 규칙</th><th>이번만</th></tr>{eps or '<tr><td colspan=5>아직 없음</td></tr>'}</table></div>
<h2>피드백 (최근 순)</h2>{''.join(items) or '<p class="meta">아직 받은 피드백이 없습니다.</p>'}
<h2>규칙 카드 (놓침·지적 많은 순)</h2><div class="wrap"><table><tr><th>id</th><th>규칙</th><th>지적</th><th>놓침</th></tr>{cards or '<tr><td colspan=4>규칙집 없음</td></tr>'}</table></div>
<h2>전달 지표</h2><div class="wrap"><table><tr><th>날짜</th><th>회차</th><th>관문</th><th>전달</th><th>자동 수정</th><th>이후 지적</th><th>놓침</th><th>비용($)</th><th>분</th></tr>{mt or '<tr><td colspan=9>아직 없음</td></tr>'}</table></div>
</main></body></html>"""
    out = P / ".board" / "feedback.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out.relative_to(P))


if __name__ == "__main__":
    main()
