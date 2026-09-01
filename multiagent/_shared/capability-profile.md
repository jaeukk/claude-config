# Capability Profile — 슬롯 → 워커 배정 (가변층)

`routing.md`의 decision tree가 정하는 **능력 슬롯을 현재 어떤 워커가 맡는지**의 정본.
신모델 출시·판정 변경 시 **이 파일만 갱신**한다(근거·날짜 필수, 이력 append-only).
모델 식별자 자체의 표기·갱신은 `backends.json`·config 소관(design-basis D7) — 여기서는 배정만 다룬다.

## 현재 배정

| 슬롯 | 담당 워커 | 배정 근거 요약 |
|------|----------|--------------|
| strategist | claude-main (경량은 Orchestrator 직접) | 설계·UI/UX 디자인·전략·문체 우위 |
| engineer | codex-main | 대규모 구현·테스트 철저, 비용·속도·토큰 효율 우위 |
| computer-use | codex-main | 브라우저 조작·복잡 워크플로우 수행 우위 |
| reviewer | codex-critic | 교차 벤더 독립 검증 (자기검수 회피) |
| multimodal | *(비활성)* → claude-main 또는 codex-main | gemini 비활성화 (D14) |
| document-reading | *(비활성)* → codex-main → claude-main | gemini 비활성화 (D14). Claude·Codex 주간 한도를 쓴다 |

## 배정 이력 (append-only)

- **2026-07-13** 초기 배정 + computer-use 슬롯 신설. 근거: 외부 리뷰 10건 종합 판정
  (Anthropic 최신 플래그십 vs OpenAI 최신 플래그십) — 디자인·전략·글쓰기 = Claude 우위,
  대규모 구현·테스트·브라우저 조작·비용·속도 = GPT 우위로 수렴. 요지는 design-basis D9.

- **2026-07-31** 위 「현재 배정」 표는 **변경 없음**. gemini는 System A에서 여전히 multimodal
  단독 담당이다. 이날의 확장은 **System B 바인딩 한정** — `agy-multimodal`·`agy-fast`가
  `bulk_worker`·`runner` 풀과 `critic`·`verifier`의 3순위 후보로 편입되었다. 근거: 2-family
  구성에서 different-family 후보가 역할당 1개뿐이라 벤더 장애가 독립 검증을 정지시키는
  단일 실패점 제거(design-basis D11). 성능 판정에 따른 재배정이 아니므로 표를 손대지 않는다 —
  Flash 티어의 저·중 추론 슬롯 승격 여부는 `tasks/agy-integration/artifacts/benchmark-plan.md`의
  사전 고정 결정 규칙으로 별도 판정한다(미실행).

- **2026-07-31 (2차)** `document-reading` 슬롯 **신설** → `gemini-reader`(agy, `gemini-3.6-flash-low`).
  근거: step 1 실측 48콜, {readable PDF, image} × {word-heavy, equation-heavy} 4케이스 ×
  4 arm × 3 repeat, 실패 0 (`tasks/agy-doc-benchmark/results/main/`). 정확도 g36-low 98.8%
  (유일 오답은 accept 목록이 좁았던 문항 결함 — 실질 100%) · codex-low 100% · claude-low 94.0%.
  **천장효과로 정확도는 변별력이 없으므로 "gemini가 우월"이 아니라 "동등"이다** — 배정 근거는
  성능 우위가 아니라 **쿼터 경제**다: agy는 별도 계정이라 **Claude·Codex 주간 한도를 소모하지
  않는다**(agy 자체 쿼터는 소모하므로 "0 소모"가 아니다). 우선순위 = gemini-reader 먼저,
  실패·부적합 시 codex-main → claude-main.

  **정정 3건 (codex-critic Sol xhigh 감사, 2026-07-31 반영)**:
  (a) `gemini-3.6-flash-medium` "완전열위" 표현은 **사전등록 채점 기준으로는 성립하지 않는다** —
      공식 점수는 g36-med 100% > g36-low 98.8%였고, 열위 주장은 E5를 문항 결함으로 본
      **사후 민감도 분석**에 의존한다. 배제 근거는 정확도가 아니라 **토큰·지연 2배**로 한정한다.
  (b) 근거 범위는 **문서 2건·문항 14개**이며 3회 반복은 새 문서에 대한 독립 표본이 아니다.
      정확한 진술은 "일반적 동등성 입증"이 아니라 **"이 두 문서에서는 차이를 검출하지 못했다"**.
  (c) 이 배정은 **아직 실행 불가**다 — `jq` 미설치로 디스패처가 모델 호출 전에 exit 5.

  문서읽기 외 저추론 과제(step 2, 117콜): 3 arm **전부 동일 100%**(공식 96.7%, 유일 오답 D04는
  정규화기가 정답 "A"를 불용어로 지운 하네스 버그 — 수정 완료). 토큰은 codex-low 47.8k ≪
  g36-low 442k ≪ claude-low 1,259k($0.45)로 **codex-low가 9~26배 효율적**이다. 따라서
  문서읽기 외 슬롯에 gemini를 우선 배치할 근거는 없으며 bindings 순서를 바꾸지 않았다.

- **2026-07-31 (3차)** `document-reading` 슬롯을 **모델 독립 계약**으로 정형화 (vault task `^bdmport`).
  표는 변경 없음 — 담당 워커가 아니라 **계약의 소재**가 바뀐 것이다.

  읽기·추출 규칙(페이지 완전 커버 · 수식 전량 · `\tag{}` · 페이지 furniture · 자기보고)이
  subagent 정의 4개와 드라이버 스크립트에 **중복 서술**되어 있었고, 실제로 **표류했다**:
  vault `paper-reviewer.md`에는 나머지 셋이 가진 수식 완전성·`\tag{}`·furniture 규칙이
  **누락된 상태**였다. 중복된 산문은 표류를 diff로 잡을 수 없다 — 그 파일들은 호스트 계층이
  **서로 달라야 정상**이기 때문이다. 단일 계약은 checksum으로 잡힌다.

  - **계약 정본**: `20_Notes/_shared/contracts/document-note.md` (vault 상주 → 홈·vault 양
    변종과 드라이버가 모두 도달 가능). 호스트 무관 — 도구·경로·파일쓰기를 일절 언급하지 않는다.
  - **Claude 호스트 어댑터**: `.claude/agents/{book-summarizer,paper-reviewer}.md` 4개
    변종이 런타임에 계약을 **읽는다**. 각 변종은 Zotero 해석·경로 변환·frontmatter·wikilink·
    figure·검증·파일쓰기만 보유 (변종 간 차이는 여기에만 존재).
  - **Gemini 호스트 어댑터**: `_shared/adapters/gemini_raw_build.py` — agy는 MCP가 없고
    일회용 cwd에서 돌므로 계약을 **인라인**한다. `run_sections.py`(landau 실증)의 정형화.
  - 남은 비대칭: agy 경로는 Zotero·vault에 도달할 수 없으므로 **Orchestrator가 전후를 감싼다**.
    이는 결함이 아니라 분리의 정의다 — 계약이 담당하는 것은 "읽기"뿐이다.

- **2026-08-26** `agy`(gemini) **전면 비활성**. `multimodal`·`document-reading` 두 슬롯의 담당을
  codex-main → claude-main으로 되돌린다. 근거: 사용자 지시. 성능 판정 변경이 아니라 **백엔드 철회**다 —
  두 슬롯의 *정의*는 그대로 두고 담당만 비운다. 제거 범위: `_shared/backends.json`의 `gemini`·
  `gemini-reader` 워커, `policy/bindings.yaml`의 `agy-multimodal`·`agy-fast` 후보(critic·verifier
  3순위, bulk_worker·runner 풀). 남긴 것: `policy/backends.yaml`의 `agy-*` 레지스트리 항목과 엔진의
  `_agy_cli` 빌더 — 어떤 바인딩도 참조하지 않아 도달 불가하며, 복원 시 재작성을 피하려 보존한다.
  **대가**: 2026-07-31에 기록한 배정 근거(별도 agy 계정이라 Claude·Codex 주간 한도를 소모하지 않음)가
  사라진다. 문서읽기가 이제 Claude·Codex 쿼터를 쓴다. 또한 D11의 3번째 family 장애 대비 후보가
  없어져 critic·verifier의 different-family 후보가 다시 각 1개다(D14).

## 갱신 절차

1. 새 판정 자료 확보 (리뷰 종합 · 벤치마크 · 자체 실측)
2. 「현재 배정」 표 갱신 + 「배정 이력」에 날짜·근거 추가 (기존 이력 삭제 금지)
3. 담당명 병기 사본을 **전부** 이 표와 동기화 — `routing.md`(트리 · Worker 역할 상세의 슬롯 표기 · 최소 Worker Set), `CLAUDE.md`(Architecture 워커 풀), `README.md`(Workers 목록), `.claude/agents/claude-main.md`(description·역할). 병기는 편의 사본 — 슬롯 정의는 불변
4. 시스템 구조 파일(orchestrator-rules·invariants 등)은 손대지 않는다
