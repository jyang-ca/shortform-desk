# shortform-desk

AI 숏폼 시리즈를 만드는 Claude Code 플러그인입니다. 대본과 시리즈 설정을 넣으면 인물 시트·키프레임·영상·내레이션·미리보기까지 만듭니다. **검수 에이전트가 통과시킨 결과물만** 전달합니다. 사용자가 피드백을 주면 그 지적을 **규칙으로 일반화**해서 다음 회차부터 미리 막습니다. 결과물과 피드백은 보드(대시보드)에 쌓입니다.

- **데스크 하나와 대화**: 지시와 피드백은 모두 `desk` 스킬(검증 데스크)에 합니다. 작가·촬영감독·검수 에이전트 9개는 데스크가 부립니다.
- **시리즈 설정은 묻는 방식으로 채움**: 이 플러그인에는 특정 시리즈의 설정이 없습니다. `series-setup`이 제목·형식·인물·장소·화풍·생성 서비스·목소리·예산·지킬 것을 차례로 묻고 파일로 적습니다.
- **공용 규칙집**: 실제 연재 시리즈를 만들며 쌓은 사용자 피드백을 다른 시리즈에도 통하는 규칙(R-…)으로 정리해 시작판으로 넣었습니다. 예: 손에 든 물건의 무게, 원근, 시점 전환, 시각 템포, 안전 검사 대응, 음량. 시리즈마다 S-… 규칙이 더해집니다.
- **생성은 확인 뒤에만**: 돈이 드는 생성은 drama-skills 방식(미리보기 → 확인 → 실행)으로만 합니다. 묻지 않고 다시 만드는 범위(`검수/권한.json`)는 사용자가 정하고 승인합니다.

## 설치

필요한 것: macOS, Claude Code, Python 3.9+, ffmpeg(`brew install ffmpeg`)

GitHub에서 받을 때(터미널의 Claude Code 프롬프트에서, `y`로 마켓플레이스 추가 → 범위 고르기):
```
/plugin install shortform-desk --marketplace jyang-ca/shortform-desk
```
셸에서: `claude plugin install shortform-desk --marketplace jyang-ca/shortform-desk`

폴더로 받았을 때(셸에서):
```
claude plugin marketplace add /path/to/shortform-desk
claude plugin install shortform-desk@shortform-desk
```
설치한 뒤 Claude Code를 다시 열면 스킬은 `/shortform-desk:desk`, `/shortform-desk:series-setup`으로 부를 수 있습니다. 에이전트는 `shortform-desk:writer`처럼 보입니다.

## 처음 한 번

1. 빈 작업 폴더에서 Claude Code를 열고 이렇게 말합니다: `/shortform-desk:series-setup 새 시리즈를 시작하자`
2. 데스크가 먼저 환경 점검을 돌립니다(`tools/setup/doctor.py`). 빠진 것을 알려 주면 채웁니다.
   - **이미지(Codex)**: `npm i -g @openai/codex` 뒤 `codex login`(ChatGPT 계정, 구독 한도 안에서 장당 과금 없음. 하루 수십 장 한도)
   - **영상(sume)·내레이션(ElevenLabs)**: 각자 키를 받아 키체인에 넣습니다. 키는 채팅에 붙여 넣지 않습니다.
     ```
     security add-generic-password -a "$USER" -s SUME_API_KEY -w
     security add-generic-password -a "$USER" -s ELEVENLABS_API_KEY -w
     ```
     (대체용 Gemini는 `GEMINI_API_KEY`)
   - **움직임·템포 검사용 파이썬 환경**: `python3 <플러그인>/tools/setup/doctor.py --install-venv`
3. 질문에 답합니다. 기획서·대본·참고 그림이 있으면 먼저 주세요. 이미 답이 있는 것은 묻지 않습니다.
4. 끝나면 프로젝트 폴더에 `시리즈/설정.json`, `시리즈/바이블.md`, `검수/규칙집_v01.md`, `CLAUDE.md`가 생깁니다.

## 매일 쓰는 법

프로젝트 폴더에서 Claude Code를 열고 데스크에 말합니다.
- "주인공 인물 시트부터 만들자" → 시트 후보 → 검수 → 통과한 것만 보여 줌 → 승인하면 모든 샷의 참조가 됨
- "1화 대본 써줘" → 작가 → 개연성·연속성·취향 검수 → 초안(`输入/대본초안/EP001/`) → 확인하면 `剧集/EP001/剧本.md`
- "1화 영상 만들어줘" → 샷 표·촬영 지시 → 사전 검사 → 키프레임·영상·내레이션 → 미리보기 → 검수 → 통과한 미리보기
- 피드백: "#012 컵이 손에서 떠 있어" → 기록 → 규칙으로 일반화(또는 '놓침'으로 검사 강화) → 고쳐서 다시 전달

보드는 `<프로젝트>/.board/index.html`을 브라우저로 엽니다. 미리보기, 샷 타임라인, 샷별 키프레임·영상·판정을 볼 수 있습니다. '피드백·규칙' 탭에서는 받은 피드백과 그것이 된 규칙, 놓침 수, 전달 지표를 봅니다. 보드는 보기 전용입니다. 조작은 채팅으로 합니다.

## 들어 있는 것

| 경로 | 내용 |
|---|---|
| `skills/desk` | 검증 데스크(받기 → 예방 → 생성 → 한 번 검수 → 고치기 → 통과한 것만 전달 → 지표) |
| `skills/series-setup` | 시리즈 설정 인터뷰 |
| `skills/short-drama*` | drama-skills 12개(MIT, 수정 없이 동봉): 기획·대본·설정·분镜·프롬프트·생산·편집·검토 |
| `agents/` | writer, why-asker, continuity, script-reviewer, dp, prompt-inspector, keyframe-inspector, edit-inspector, pm |
| `tools/adapters` | codex-image, sume-video, elevenlabs-tts, gemini(image/video) 어댑터와 오프라인 모의 시험 |
| `tools/pipeline` | job 만들기(이미지·영상·TTS), 샷표, 미리보기 조립, 촬영 지시 조립, 밀기 끝 프레임, 장소별 색 맞춤 |
| `tools/qa` | 프롬프트 린트, 미리보기 자동 검사, 컷 문법·시각 템포·움직임 측정, 프레임 띠 |
| `tools/board` | 제작 보드, 피드백·규칙 페이지, 클립 묶음 |
| `tools/setup` | 환경 점검(doctor), 새 시리즈 만들기(init_series) |
| `templates/` | 설정·바이블·대본 지침·공용 규칙집·카메라 말 장부·결정표·권한의 시작판 |

## 비용 감각 (2026-11 기준, 실제 제작 기록)
- **영상**: sume Gemini Omni Flash 360p는 초당 약 $0.023입니다. 한 화(약 40샷) 첫 판은 약 $3~8, 고치기를 넣으면 $8~15입니다. 실패한 생성은 환불됩니다.
- **이미지**: Codex는 구독 한도 안이고, 한 화에 30~60장입니다. 한도에 걸리면 몇 시간 잠깁니다.
- **내레이션**: ElevenLabs 구독 크레딧 안입니다. 동시 요청은 등급에 따라 2개 안팎입니다.

## 시험
```
python3 tools/setup/doctor.py --selftest     # 환경 + 어댑터 4개 모의 시험(돈 안 듦)
python3 tools/setup/e2e_test.py                # 가짜 시리즈로 린트 → 미리보기 → 자동 검사 → 보드까지(돈 안 듦)
claude plugin validate .
```

## 알아 둘 것
- 다른 작품의 인물·그림·이름을 가져다 쓰지 않습니다. 참고 채널은 측정·공부용입니다.
- 결과물 링크(sume `media.sume.com`)는 링크를 아는 사람에게 공개됩니다.
- 플러그인을 업데이트하면 설치 경로가 바뀝니다. 데스크가 세션마다 doctor로 어댑터 설정을 다시 맞춥니다.
- 라이선스: MIT(`LICENSE`). 동봉한 drama-skills도 MIT — 출처는 `third_party/NOTICE.md`
