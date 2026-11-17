#!/usr/bin/env python3
"""새 시리즈 프로젝트 만들기 / 설정 채우기 / 빈 칸 보기.

python3 <TOOLS>/setup/init_series.py <project> --title "제목" [--answers answers.json]   # 만들기(있으면 채우기만)
python3 <TOOLS>/setup/init_series.py <project> --answers answers.json                   # 설정에 답 합치기
python3 <TOOLS>/setup/init_series.py <project> --missing                                # 아직 안 정한 칸(인터뷰가 물을 것)

하는 일
- short-drama.json이 없으면 drama-skills project_tool.py init(제목·언어·화면비·화수·목표 길이)
- 시리즈/설정.json: 템플릿 + answers(부분 JSON, 같은 키 구조)를 합친다. 목록(characters·locations·props)은 answers가 통째로 바꾼다
- 시리즈/바이블.md·대본-지침.md, 검수/{규칙집_v01.md, 카메라문장_장부.json, 현재-결정표.json, 권한.json}: 없을 때만 복사
- 검수/{피드백-기록,지표,막힌-문장}.jsonl: 없으면 빈 파일
- CLAUDE.md: 없을 때만(이 폴더에서 Claude Code를 열면 데스크로 일하게)
기존 파일은 덮어쓰지 않는다(설정.json만 합친다).
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

REQUIRED = [  # (키 경로, 사람이 읽는 이름)
    ("title", "시리즈 제목"), ("logline", "한 줄 로그라인"), ("genre", "장르"),
    ("format.narration", "말 방식"), ("format.max_seconds", "한 화 길이 상한"), ("format.episodes_planned", "계획 화수"),
    ("look.style", "화풍"), ("look.style_line", "프롬프트 화풍 줄"),
    ("characters[0].name", "주인공 이름"), ("characters[0].image_sentence", "주인공 이미지 문장"),
    ("characters[0].video_sentence", "주인공 영상 문장"), ("locations[0].name", "주 장소"),
    ("production.image_adapter", "이미지 서비스"), ("production.video_adapter", "영상 서비스"),
]
NARRATED = [("production.voice_id", "내레이션 목소리 ID")]

CLAUDE_MD = """# {title} — shortform-desk 프로젝트

이 폴더는 shortform-desk 플러그인의 시리즈 프로젝트다.
- 제작 지시·피드백은 모두 `shortform-desk:desk` 스킬(검증 데스크)로 처리한다. 사용자는 데스크에만 말하고, 데스크는 검수를 통과한 결과만 전달한다.
- 시리즈 값은 `시리즈/설정.json`, 세계·인물·톤은 `시리즈/바이블.md`, 규칙은 `검수/규칙집_v*.md` 최신 판.
- 설정이 비었거나 바꾸려면 `shortform-desk:series-setup`.
- 외부 생성(돈이 드는 것)은 `검수/권한.json` 승인 범위 안에서만 묻지 않고, 밖이면 먼저 묻는다. 키는 파일에 쓰지 않는다.
- 보드: `.board/index.html`(보기 전용, 피드백·규칙 탭 포함).
"""


def get(d, path):
    cur = d
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            i = int(part)
            if i >= len(cur):
                return None
            cur = cur[i]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base.get(k), v) if k in base and isinstance(v, dict) else v
        return out
    return over


def missing(S):
    req = REQUIRED + (NARRATED if get(S, "format.narration") not in ("none", "", None) else [])
    return [{"key": k, "what": w} for k, w in req if get(S, k) in (None, "", [], 0)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project"); ap.add_argument("--title"); ap.add_argument("--answers"); ap.add_argument("--missing", action="store_true")
    a = ap.parse_args()
    P = Path(a.project).expanduser().resolve()
    T = common.TEMPLATES
    sp = P / "시리즈" / "설정.json"
    if a.missing:
        S = common.load_series(P)
        print(json.dumps({"missing": missing(S)}, ensure_ascii=False, indent=1))
        return
    ans = json.loads(Path(a.answers).read_text(encoding="utf-8")) if a.answers else {}
    title = a.title or ans.get("title")
    created = []
    if not (P / "short-drama.json").exists():
        if not title:
            raise SystemExit("새 프로젝트에는 --title(또는 answers.title)이 필요하다")
        P.mkdir(parents=True, exist_ok=True)
        F = ans.get("format", {})
        cmd = [sys.executable, str(common.PLUGIN_ROOT / "skills" / "short-drama" / "scripts" / "project_tool.py"), "init", str(P),
               "--title", title, "--language", ans.get("language", "ko-KR"), "--prompt-language", ans.get("prompt_language", "en"),
               "--aspect-ratio", F.get("aspect", "9:16")]
        if F.get("episodes_planned"):
            cmd += ["--episode-count", str(F["episodes_planned"])]
        if F.get("target_seconds"):
            ts = F["target_seconds"]
            cmd += ["--target-seconds", str(ts[1] if isinstance(ts, list) else ts)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"drama-skills init 실패: {r.stderr.strip()[-800:]}")
        created.append("short-drama.json (drama-skills init)")
    base = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else json.loads((T / "시리즈" / "설정.json").read_text(encoding="utf-8"))
    if title:
        ans = {**ans, "title": title}
    S = merge(base, ans)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(S, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for rel in ("시리즈/바이블.md", "시리즈/대본-지침.md", "검수/규칙집_v01.md", "검수/카메라문장_장부.json", "검수/현재-결정표.json", "검수/권한.json"):
        dst = P / rel
        if rel.startswith("검수/규칙집") and common.latest_rulebook(P):
            continue
        if not dst.exists() and (T / rel).exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            txt = (T / rel).read_text(encoding="utf-8")
            dst.write_text(txt.replace("{{title}}", S.get("title") or ""), encoding="utf-8")
            created.append(rel)
    for rel in ("검수/피드백-기록.jsonl", "검수/지표.jsonl", "검수/막힌-문장.jsonl"):
        dst = P / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True); dst.write_text("", encoding="utf-8"); created.append(rel)
    if not (P / "CLAUDE.md").exists():
        (P / "CLAUDE.md").write_text(CLAUDE_MD.format(title=S.get("title") or P.name), encoding="utf-8"); created.append("CLAUDE.md")
    print(json.dumps({"project": str(P), "created": created, "missing": missing(common.load_series(P))}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
