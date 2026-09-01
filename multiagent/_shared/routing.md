# Worker Routing Rules

## 2층 라우팅 — 안정층/가변층

이 파일의 decision tree는 **작업 유형 → 능력 슬롯**을 정한다(안정층 — 모델 세대가 바뀌어도 유효).
**슬롯 → 담당 워커 배정**의 정본은 `_shared/capability-profile.md`(가변층)다.
신모델 출시·판정 변경 시 **프로필만 갱신**한다 — 이 파일의 슬롯 정의는 손대지 않는다.
아래 트리의 워커명은 현 프로필 배정의 병기(편의 사본)다 — 프로필과 어긋나면 **프로필이 이긴다**.

## Decision Tree

```
작업 성격 파악 → 능력 슬롯 → 담당 워커 (배정 정본: capability-profile.md)
│
├── [strategist] 기획 · 설계 · 아키텍처 · 요구사항 · 전략 · UI/UX 디자인 방향
│   · 문체가 중요한 글쓰기 · 까다로운 로직 설계 · 디버깅 원인 분석?
│   └── claude-main
│
├── [engineer] 대규모 구현 · 리팩토링 · 테스트 작성·실행 · diff · 로컬 CLI 검증 · 이미지 생성?
│   └── codex-main
│
├── [computer-use] 브라우저 조작 · 복잡한 도구 워크플로우 자동화?
│   └── codex-main
│
├── [reviewer] 산출물 리뷰 / 비판적 검증?
│   └── codex-critic   (Codex의 주된 역할)
│
├── [document-reading] few-page 문서(PDF·페이지 이미지) 읽기 · 사실 추출 · 발췌?
│   └── codex-main  → 실패·부적합 시 claude-main   (gemini 비활성, D14)
│
├── [multimodal] 이미지 · 스크린샷 분석 / 50페이지+ 문서 / 제3자 시각의 검토?
│   └── claude-main (이미지 첨부 가능) 또는 codex-main   (gemini 비활성, D14)
│
└── 판단 어려움?
    └── claude-main으로 시작 후 필요 시 추가
```

## 복합 작업 우선순위

한 작업이 여러 분기에 해당할 때:

1. **선행 의존성 우선**: codex-critic은 리뷰 대상(보통 claude-main 결과)이 먼저 있어야 함 → 해당 산출물 뒤에 호출
2. **Orchestrator 내부 추론 우선**: 별도 worker 호출 전에 orchestrator 자체 추론으로 해결 가능한지 먼저 판단. 그래도 부족할 때만 claude-main 호출 (claude-main도 비용·쿼터 대상)
3. **검증은 한 번만**: codex-critic은 작업당 1회 원칙. 재호출은 검증 실패 시만
4. **gemini는 비활성**(D14): worker pool에서 제거됐다. 멀티모달·문서읽기는 claude-main/codex-main이 맡는다 — Claude·Codex 주간 한도를 쓰므로 필요할 때만 호출

## 토폴로지 패턴 (worker를 어떻게 엮을까)

decision tree로 "누구를" 고른 뒤, "어떻게 엮을지" 고른다. **단일 orchestrator 구조에 맞는 4패턴만** 쓴다.

| 패턴 | 언제 | 이 시스템에서 |
|------|------|-------------|
| Pipeline (순차) | 앞 결과가 뒤 입력 | 기본. claude-main → codex-critic → claude-main(반영) |
| Fan-out/Fan-in (병렬→통합) | 서로 독립된 산출물 여럿을 하나로 통합 | 예: claude-main(코드) ∥ codex-main(테스트). 각 brief에 "타 worker 결과 미참조" 명시. 통합은 아래 Fan-in 규칙 |
| Expert Pool (전문가 선택) | 작업 성격에 맞는 worker만 | 새 실행 패턴이 아니라 **worker 선택 정책** — 위 decision tree + 최소 worker set이 곧 이 패턴 |
| Producer-Reviewer (생성+게이트) | 산출물 품질 검증 필요 | claude-main(생성) → codex-critic(adversarial 게이트) |

**금지**: 같은 입력에 같은 종류 worker 동시 호출 (예: claude-main 2개).
**배제**: Supervisor(별도 long-lived 조정자 worker/런타임 동적분배 계층 추가)·Hierarchical Delegation(worker가 worker를 부르는 재귀 위임)은 단일 orchestrator·worker간 무통신·file-as-memory와 충돌 → 미사용. 근거: design-basis D6.

### Fan-in 규칙 (병렬 결과 통합)

병렬 worker 결과를 orchestrator가 하나로 합칠 때:
1. 각 worker 원문을 `result.md`에 그대로 보존 (요약본만 남기지 말 것 — telephone game 방지)
2. 결과가 충돌하면 삭제 금지 → 양쪽 출처 병기, 권위 우선순위/사실검증으로 해소, `log.md` [DECISION]에 근거 기록
3. 통합 결론 한 줄을 `context.md`에 기록

## Worker 역할 상세

### claude-main
- **슬롯**: strategist
- **용도**: 기획, 요구사항 정의, 설계 문서, 사용자 스토리, 아키텍처, 전략 수립, UI/UX 디자인 방향, 문체가 중요한 글쓰기, 까다로운 로직 설계, 디버깅 원인 분석, (설계와 분리 곤란한) 핵심 구현
- **결과물**: 코드 (구현·수정·diff), 설계 문서, 구조도, 의사결정 근거
- **호출 명령**: Claude Code 내장 **Task tool (sub-agent)**
  - `subagent_type`: `claude-main` (`.claude/agents/claude-main.md`에 정의)
  - `prompt`: brief.md 내용 그대로 전달
  - `model`: agent 정의 파일 frontmatter의 `model: opus`가 자동 적용 (별칭 — 현재 환경의 Opus로 해석. 버전 문자열 핀하지 않음. 모델 정책 참조)
  - `description`: 짧은 작업명 (3~5 단어)
- **권한**: 메인 Claude Code 세션의 권한 모드 상속. `--dangerously-skip-permissions` (yolo) 모드면 sub-agent도 yolo로 작동. 단 MultiAgent 시스템 게이트(`workers_approved`, 외부 쓰기 4조건)는 별개로 유지된다
- **비용**: 있음 (Opus(`opus` 별칭) sub-agent 호출. 별도 모델 호출이며 비용·쿼터 대상) → 승인 필요
- **파일 쓰기**: ❌ 직접 X. Task tool이 반환한 텍스트를 Orchestrator가 받아 `result.md`에 기록
- ※ Orchestrator의 내부 추론과 다름.

### codex-main
- **슬롯**: engineer · computer-use
- **용도**: 대규모 구현·리팩토링 (claude-main 설계 기반 또는 단독), 코드베이스 분석, 테스트 작성·실행, diff 생성, 로컬 CLI 검증, 브라우저 조작·도구 워크플로우 자동화, 이미지 생성 (Codex 내장 `image_gen` 도구)
- **결과물**: 코드, diff, 테스트 결과, CLI 출력, PNG/SVG 이미지
- **호출 명령**: `mcp__codex__codex` MCP 도구
  - `prompt`: brief.md 내용 그대로 전달
  - `cwd`:
    - 기본: `<설치한-폴더>/tasks/<task>/` — 이 안에서 산출물·diff 직접 작성
    - 외부 쓰기 4조건 충족 시: brief.md의 `target_repo` 값으로 변경
  - `sandbox`: `workspace-write` 고정 (cwd 내부만 쓰기 가능. cwd 밖은 sandbox가 차단)
  - `approval-policy`: `on-failure` 권장
- **brief 필수 필드** (오케스트레이터가 사용자에게 target_repo를 먼저 묻고 답을 받아 채운다 — 분석·리뷰·요약 작업은 예외):
  ```yaml
  target_repo: /absolute/path/to/repo                   # 작업 대상 절대 경로 (없으면 N/A)
  write_scope: none | tasks-only | "src/**, tests/**"   # none=쓰기금지 / tasks-only=tasks/<task>/ 내부만(codex-main 기본) / 패턴=외부 repo 해당 경로(외부는 4조건)
  ```
- **비용**: 있음 (Codex 호출 쿼터) → 승인 필요
- **파일 쓰기**:
  - 기본: cwd=`tasks/<task>/` + sandbox=`workspace-write` → 작업 폴더 내부 산출물·diff 직접 작성 가능 (외부 repo는 sandbox가 막음)
  - 외부 repo 쓰기 4조건 (CLAUDE.md "Worker 파일 쓰기 정책" 참조) 모두 충족 시에만 cwd를 `target_repo`로 변경하여 해당 scope 내 직접 쓰기 허용
  - 어느 경우에도 `_shared/`, `_templates/`, 다른 작업 폴더는 쓰지 말 것

### codex-critic
- **슬롯**: reviewer
- **용도**: 리뷰 대상 산출물(주로 claude-main 코드·설계, 또는 brief에 명시된 기존 코드·문서·소스)을 실제 repo/파일/CLI 관점에서 리뷰·비평. 실현 가능성, 비용, 테스트 커버리지, 사이드 이펙트 검토. **Codex의 주된 역할.**
- **선행 조건**: 리뷰 대상 산출물 경로가 존재 — 보통 claude-main `result.md`, 또는 brief에 명시된 기존 코드·문서·소스
- **결과물**: 비평 리스트, 수정 제안
- **호출 명령**: codex-main과 동일 (`mcp__codex__codex` MCP). 단 다음 강제:
  - `sandbox`: `read-only` 고정 (쓰기 금지)
  - brief에 "비평 모드" 명시
  - brief의 `target_repo` 명시 (비평 대상 repo 컨텍스트), `write_scope: none`
- **비용**: 있음 → 승인 필요
- **파일 쓰기**: ❌ 직접 X. Orchestrator 경유

### gemini · gemini-reader — **비활성 (D14, 2026-08-26)**

agy 백엔드를 껐다. 두 워커는 `_shared/backends.json`에서 제거됐고 `call_worker.sh`로 호출할 수 없다.
멀티모달·문서읽기는 codex-main → claude-main 순으로 대체한다. 되살리려면 backends.json 항목과
`policy/bindings.yaml` 후보를 함께 복원해야 한다.

### gemini-raw-build (다중 섹션 문서 → 노트 배치 경로) — **비활성 (D14)**

> 이 드라이버도 `agy`를 직접 호출하므로 agy 비활성화와 함께 멈춘다. 아래 기술은 되살릴 때를 위한 보존 기록이다.

`gemini-reader`가 **1콜 = 1질의**라면, 이쪽은 **1잡 = N섹션**이다. 책·장문 문서를 섹션 단위로
끊어 읽어 노트 초안을 만든다. `tasks/landau-raw-ingest/run_sections.py`(Landau §1–10, 37쪽,
Claude·Codex 주간 0%p)의 정형화.

- **드라이버**: `python3 _shared/adapters/gemini_raw_build.py <job.json>`
  (`call_worker.sh` 경유가 아니다 — 섹션 루프·재시도·usage 원장이 필요해 전용 드라이버를 쓴다)
- **읽기 규칙의 정본**: `20_Notes/_shared/contracts/document-note.md`. 드라이버는 이 파일의
  `<!-- END OF CONTRACT -->` **위쪽만** 프롬프트에 인라인한다(agy는 MCP도 vault 접근도 없다).
  ⛔ **읽기·추출 규칙을 이 스크립트나 agent 정의에 다시 쓰지 말 것** — 계약 파일만 고친다.
- **job.json 필드**

  | 키 | 뜻 |
  |---|---|
  | `document` | 프롬프트에 들어갈 문서 식별 문자열 |
  | `pdf` (선택) | 원본 PDF. 주면 페이지를 직접 렌더한다 |
  | `pages_dir` | `p<인쇄쪽>.png`의 위치 (렌더 결과 또는 기존 이미지) |
  | `page_offset` | `pdf_page = printed_page + offset` |
  | `sections` | `{number, title, start}` 배열 — **끝쪽은 주지 않는다** |
  | `end_page` | 마지막 섹션이 끝나는 인쇄 쪽 |
  | `work_dir` | 섹션별 일회용 cwd의 부모 |
  | `out_dir` | `s<NN>.md` 출력 |
  | `contract`, `model`, `dpi`, `timeout` | 계약 경로 / 기본 `gemini-3.6-flash-low` / 200 / 900 |

- **섹션 끝쪽을 받지 않는 이유**: 드라이버가 "다음 섹션의 시작 쪽"까지로 범위를 잡아 **한 쪽씩
  겹치게** 만든다. 제목 위치만으로 끊으면 공유 페이지가 **양쪽 노트에서 모두 사라진다** —
  앞 섹션의 끝 뒤이면서 뒤 섹션의 시작 앞이기 때문. 실제로 Landau §1의 (1.5)–(1.8)이 이렇게
  사라졌다. 겹침은 계약의 "문단 단위로 판단하고 경계를 보고하라"와 짝을 이룬다.
- **자기보고**: 계약이 노트 끝에 `BOUNDARY:` / `EQUATIONS:` / `ILLEGIBLE:` 3줄을 요구한다.
  드라이버가 **말미 블록만** 떼어내 원장에 넣는다(본문 어디서나 지우면 탄성론 노트의
  `BOUNDARY: u=0 at x=0` 같은 실제 경계조건이 조용히 삭제된다). 미보고는 `(not reported)`로
  **가시화**한다. 3줄이 다 있으면 **생성이 끝까지 갔다는 증거**이므로 문장부호 휴리스틱보다 우선한다.
- **재시도 분류**: `truncated`·`empty`만 재시도(최대 3회). `refusal`은 **재시도하지 않는다** —
  가드레일은 불안정한 네트워크가 아니다. 사람이 판단할 일로 보고하고 끝낸다.
  refusal 판정은 1인칭 거절 문구로 한정한다 — 맨 단어 `copyright`는 쓰지 않는다(저작권을
  *다루는* 노트가 거절로 오인되면 재시도조차 되지 않는다).
- ⛔ **`ok`만 `out_dir`에 쓴다.** refusal·truncated는 `out_dir/rejected/s<NN>.try<N>.md`로
  격리하고, 하나라도 남으면 **exit 1**. 이유: 거절 응답은 대개 *유창한 요약*이라 노트 자리에
  놓이면 정상 노트와 구별되지 않는다(landau §2가 정확히 이 사례). 실패본을 버리지 않고
  남기는 이유는 진단에 본문이 필요하기 때문 — §7이 페이지를 읽기도 전에 거절했다는 사실은
  토큰 수와 본문 내용에서 드러났다.
- ⛔ **cwd 격리**: 섹션마다 해당 페이지 이미지만 복사한 폐기용 디렉토리를 만들어 그곳을 cwd로
  준다. `gemini-reader`의 `TARGET_REPO` 규칙과 같은 이유 —
  `--dangerously-skip-permissions`가 불가피하고 `--sandbox`는 쓰기 차단이 아니다.
- **한계 (분리의 정의이지 결함이 아님)**: 이 경로는 Zotero·vault에 **도달할 수 없다**. citekey→PDF
  해석, frontmatter, wikilink, figure 캡처, 파일 배치는 **Orchestrator가 전후로 감싼다**.
  계약이 담당하는 것은 "읽기"뿐이다.

## 모델 정책

각 worker가 실제 어떤 모델로 도는지 정리. 사용자가 매번 명시할 필요는 없으며, 아래 기본이 자동 적용된다.

- **claude-main**: 별칭 **`opus`** (`.claude/agents/claude-main.md` frontmatter `model: opus`). 버전 문자열을 핀하지 않는다 — 별칭이 현재 환경의 최신 Opus로 자동 해석되므로 모델이 올라가도 갱신 불필요.
  - **추론 강도(effort)**: claude-main 정의는 frontmatter `effort: xhigh`로 **고정**(상속 끔) — 세션 `/effort`와 무관하게 동작한다. 2026-07-28 이전엔 `effort` 필드 없이 세션 상속이 기본이었다. 근거: `design-basis.md` D7 effort 절.
- **codex-main / codex-critic**: 사용자의 `~/.codex/config.toml` 기본값이 자동 적용된다 (현재 예: 최신 gpt + reasoning effort `high`). config.toml이 정본이라 여기에 버전을 핀하지 않는다. MCP 호출 시 `model` 파라미터를 비워두면 config 기본값 사용.
  - 가벼운 작업은 `profile: lightweight`로 전환 가능 (config.toml의 가벼운 모델 프로필)
  - 작업 성격상 다른 모델이 필요하면 brief.md에 명시
- **gemini**: **비활성 (D14, 2026-08-26)** — `_shared/backends.json`에서 `gemini`·`gemini-reader`를 제거했고 `policy/bindings.yaml`의 `agy-*` 후보도 뺐다. 백엔드 레지스트리(`policy/backends.yaml`)의 `agy-multimodal`·`agy-fast` 항목과 엔진의 `_agy_cli` 빌더는 남아 있으나 어떤 바인딩·`dispatch_hosts`도 참조하지 않아 도달 불가다. 되살리려면 backends.json 워커 + bindings 후보 + `dispatch_hosts`를 함께 복원하고, 복원 전에 `_agy_cli` docstring이 적어 둔 두 전제(격리 입증·완료 관측)를 확인할 것.

이 정책은 사용자별 config에 따라 달라질 수 있다 — starter clone 받은 학습자는 본인의 `~/.codex/config.toml` 기본값을 한 번 확인하고 자기 환경에 맞게 조정한다.

## 최소 Worker Set 원칙

| 작업 유형 | 권장 최소 set |
|----------|------------|
| 문서/기획/전략만 | claude-main |
| 설계 + 소규모 구현 | claude-main (설계·구현 일괄) |
| 대규모 구현·테스트 | claude-main (설계) → codex-main (구현·테스트) |
| 브라우저 자동화 / 이미지 생성 | codex-main |
| 구현 + 비평 | 생성 워커 → codex-critic → 반영 |
| 대용량 문서 처리 | codex-main (gemini 비활성) |
| 전체 검토 | claude-main → codex-critic |

모든 worker를 기본 호출하지 말 것. 필요한 worker만 선택.

## Worker 추가 조건

- 이미 있는 worker 결과로 해결 가능하면 추가 호출 금지
- 이전 결과가 검증 미통과 시에만 동일 worker 재호출 가능
- gemini는 비활성 (D14) — 제3자 시각이 필요하면 codex-critic이 그 역할을 맡는다
