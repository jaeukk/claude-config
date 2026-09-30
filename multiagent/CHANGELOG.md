# Changelog

이 파일은 MultiAgent orchestration 시스템의 주요 변경을 기록한다.
형식은 [Keep a Changelog](https://keepachangelog.com/), 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

## [Unreleased]

### Added
- `engine/adapters/codex_pretool.py`: the Claude hook's rules on Codex through its `PreToolUse` hook
  (shell, `apply_patch` per file, `spawn_agent` as family `codex`), for sessions inside `multiagent/`
  while a task is active. The Claude hook's decision is now a shared `evaluate()`.
- SKILL.md "Review discipline": agree the threat model, the blocking bar, the round plan and the stop
  rule before the first critic round (the global CLAUDE.md rule reaches Claude sessions only; Codex
  conductors read the skill).
- `dispatch-worker --review-copy` (Codex critic or verifier): a writable sandbox in a disposable
  copy of `target_repo`, so the reviewer can run the tests; the original stays read-only.

### Changed
- A Codex usage-limit exit is recorded as `rate_limited`, not `error` (only the last stderr lines are
  read).
- SKILL.md: a review round that leaves nothing to fix (no findings, or all deferred or rejected)
  dispatches no fixer (step-7 benchmark: three empty reviews each paid an unchanged $0.41 fix).
- Conductor defects seen in the bench8 benchmark (8 headless conductor sessions):
  - `release-lease` also removes `tasks/.active-task` when it names the released installation task; the
    hook refuses a conductor's own removal, so 6 of 8 left a stale pointer.
  - A conductor may write ordinary files in its active task's own folder (briefs, notes, results); engine
    state there stays refused. Before this, a critic brief could not be written mid-task at all.
  - SKILL.md: never end a turn while a dispatch you started is running (a headless session killed its
    critic); do not open other tasks' folders as examples; task IDs are lowercase.
- Fewer conductor turns (bench8: multiagent runs took 2–5× vanilla's turns, most of them ceremony):
  - `policy_engine.py review --conductor-host … --target-repo … --brief … --out … [--review-copy]`: one review round in one
    foreground call (`produce` with a critic or verifier role; the author is the conductor's family).
  - The shell rule refuses file writes only: a quoted `>`, `2>&1`, `/dev/null` and `>(…)` pass (bench8's 23
    refusals drop to the 8 real writes); refusal messages say what to do instead.
  - SKILL.md: no contract, lease or pointer when nothing is dispatched or reviewed.
  - `multiagent/CLAUDE.md`/`AGENTS.md`: reading the karpathy guidelines in full is no longer a required first step.

## [1.5.0] - 2026-09-30

Items 1–2 of the post-cut plan (`tasks/2026-09-30-multiagent-next/synthesis.md`), worked out with
one codex-ceiling consultation.

### Added
- **`policy_engine.py cost-report`** sums every `worker_attempt` event under one or more task
  roots by account and model: attempts, outcomes, Claude output tokens and `total_cost_usd`, Codex
  `tokens_used`, and `uncosted` (attempts with no cost data). Overlapping roots are read once;
  `--since YYYY-MM-DD`; `--json`.
- **`policy_engine.py record-attempt`** lets a headless driver record a worker the engine did not
  launch (`source: external`; `account`, `model`, `classification` required; no lease, like
  `record-author`). Fields are type-checked, and external events never count as an audit
  round. The book driver records each attempt (best effort) with the CLI outcome, whether the
  chapter was built, and the envelope's usage and cost.
- **`policy_engine.py produce`** is the one-producer route: contract (one planned producer,
  `audit_cycles` 0), lease, dispatch (`--write [--exec]` or `--out`), release, and a summary on
  stderr, in one call. It validates the policy and the contract, creates the task folder
  exclusively after reading the brief (an existing folder or link is refused), writes the contract
  `pending`, activates it and later finalizes it only under the lease lock while this run owns the
  lease, and never writes it when the lease was not acquired; cleanup never raises. It deletes
  nothing but its own lease file (generation-checked) and its own `lease.lock` (identity-checked): a setup that fails after creating the folder
  leaves it as a record (rerun with another task ID), the brief is created exclusively, and the
  hidden `.task-initial.*.json` hard link to the first contract stays. `--dry-run` creates nothing.
- Contract status `failed` is now valid (the benchmark driver already wrote it).

### Changed
- SKILL.md: native spawns choose the type and model by tier (`Explore`/`runner` for lookups;
  `model: "haiku"`/`"sonnet"` on `Agent` for shards and routine production). A `general-purpose`
  spawn with no model otherwise runs at the session's model on the private login. Also added: a rule
  for when to push a job to the team account, and the cost commands. SKILL.md is 2,882 words
  (1.4.0: 2,598).

## [1.4.0] - 2026-09-30

Cut program, stages 2–3 (`tasks/2026-09-29-cut-stage2-3/plan.md`; design-basis D17). Stage 1 found
that a single session matched an implementer → critic → fix loop on test-oracle code within noise
at 2–3.7× lower cost, so the engine becomes opt-in.

### Changed
- **Single session is the default.** `skills/multiagent/SKILL.md` rewritten (7,088 → about 2,600
  words): the engine is for cross-vendor review of consequential or no-oracle work, team-account
  routing, and contained writes or publication with a record. Authorship is stated once; the Orca
  and Gemini sections are gone; stale claims fixed (Codex has PreToolUse hooks but no multiagent
  adapter yet; the hook matcher covers `Agent` and `Task`; `fork_turns` defaults to `all`; a Codex
  critic or verifier usually cannot run a suite; Codex-authored work's verifier is
  `claude-mid-team` first).
- **`AGENTS.md` / `CLAUDE.md`** are now an English maintainer file (tests, self-test, the
  edit-in-a-clone rule, where lessons go). The System A rules moved to
  `_archive/system-a/AGENTS.md`; the remaining System A files are labeled legacy.
- `docs/task-contract.md` absorbs `references/policy-layout.md` (now a pointer). README fixes: the
  implementer tier, "an unreadable probe skips team" (it routes to team), and
  `CLAUDE_TEAM_MAX_PERCENT` (nothing reads it); the WSL paragraph and the picture-book pointer
  moved in.
- **Mid tier re-pinned to Sonnet 5.5** — `claude-mid` and `claude-mid-team` now pin
  `claude-sonnet-5-5` (medium), and the book driver defaults to it. Evidence: the 2026-09-29 local check
  (capability profile) and the vendor comparison. Core stays on Opus 5.5.

### Added
- **Audit budget `audit_cycles`** (design-basis D16) — critic rounds a task may run; absent or 0
  skips audit. Enforced by `dispatch-worker` (zero or spent budget refuses a critic) and
  `validate-task`; the hook refuses a native critic at 0. Templates default to 0.
- `dispatch-worker --write <dest> --exec`: Bash with a named command allowlist for a Claude write
  worker. Bash writes are not confined to the destination and are outside the change set; the
  recorded `enforcement` says so.
- `worker_attempt` events carry cost: the Claude envelope's `usage` and `total_cost_usd`, and
  Codex's `tokens used`.

### Removed
- The `native_reason` gate, the `direct_code_files` cap (`approvals.yaml` `direct_conductor_edit`),
  the native fan-out count from `dispatch.active_workers`, and `dispatch-worker`'s
  `current_role == --role` check. Contracts carrying these fields are accepted and ignored; the
  hook still needs `dispatch.current_role` before a native spawn.
- `--host wsl`; the agy/Gemini builder, backends and `--required-family gemini` (revive from
  `8e57af8`); policy keys nothing read. Archived with `git mv`: the Orca adapter,
  `install_wsl_orchestration.js`, `gemini_api.sh`, `gemini_raw_build.py`, and `docs/final-plan.md`,
  `docs/architecture.md`, `docs/team-account-routing.md` (to `_archive/`).

### Fixed
- The CLI no longer accepts abbreviated options, and `acquire-lease` / `append-event` refuse a
  folder with no `task.yaml` (the 2026-09-27 stray task folder).

## [1.3.1] - 2026-09-29

### Changed
- **CLAUDE.md / AGENTS.md "운영 원칙"** — the verbatim copy of the four principles is replaced by a pointer to the
  `karpathy-guidelines` skill file, the one authoritative copy. The success-indicator line, the layering rule and the
  attribution stay. NOTICE, design-basis D8 and INV12 (new INV12g: the skill file exists) updated.

## [1.3.0] - 2026-07-13

### Added
- **라우팅 2층 분리 — `_shared/capability-profile.md` 신설(가변층)** — 능력 슬롯
  (strategist·engineer·computer-use·reviewer·multimodal) → 담당 워커 배정의 정본.
  신모델 출시·판정 변경 시 프로필만 갱신(근거·날짜 필수, 이력 append-only) — routing.md의
  슬롯 정의는 불변. 근거: design-basis D9 (2026-07-13 외부 리뷰 10건 종합 판정).
- **computer-use 슬롯 신설** — 브라우저 조작·도구 워크플로우 자동화를 독립 라우팅
  (현 배정: codex-main).

### Changed
- routing.md decision tree를 슬롯 기반으로 재편 — strategist(기획·설계·디자인·전략·문체)
  = claude-main, engineer(대규모 구현·테스트) = codex-main. 종전 "메인 코딩=claude-main,
  보조 구현=codex-main" 구도에서 무게중심 이동. 최소 worker set 표 동기화.
- validate에 C5b(2층 라우팅: routing→profile 참조 + 슬롯 5종) 추가, C1에 프로필 포함.

## [1.2.2] - 2026-07-04

### Fixed
- **gemini 워커 폴백 실패 사유 유실** — 디스패처(`call_worker.sh`)가 api 폴백의 필수 env
  (`GEMINI_API_KEY`) 부재 시 실패 사유 없이 죽던 문제를 에러 envelope 반환으로 수정,
  호출 시작 시 폴백 불가 사전 경고 추가.

### Changed
- routing.md gemini — 소스·다중파일 검토 인라인 필수(agy 헤드리스 300s 타임아웃 실측),
  폴백 조건(`GEMINI_API_KEY`) 명문화, 시간 제한 작업 전 경량 스모크 권장.

## [1.2.1] - 2026-07-03

### Fixed
- **gemini(agy) 워커 프롬프트 미전달 수정** — Antigravity CLI 1.0.16에서 `-p` 단축 플래그가
  제거되어 backends.json의 `args_template: ["-p", …]`가 프롬프트를 조용히 무시(모델 미호출·사용량 0).
  `["--prompt", …]`로 교정. 증상: gemini 워커가 온보딩 인사만 반환.

## [1.2.0] - 2026-06-28

### Added
- **opt-in goal 요금가드 배선(`--with-guard`)** — 설치 시 `--with-guard`를 주면 `.claude/settings.json`에
  Stop 훅(`coach --hook`)이 주입된다. `/goal` 자율 루프가 주간 사용량 한도에 닿으면 자동 정지(루프
  중에만 — `stop_hook_active` 게이트). 기본 미설치, 런타임 on/off=`coach guard on/off`. 정책은 `coach`
  (usage-coach, codexbar 의존)가 갖고 미설치·조회실패는 fail-open(작업 안 죽임).

## [1.1.0] - 2026-06-10

카파시(Karpathy) 4원칙을 층별로 도입. 기존 규칙과 충돌 없음(보강).

### Added
- **CLAUDE.md "운영 원칙 (Operating Principles)" 섹션** — 4원칙(Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution) verbatim 차용 + 층별 적용 규칙. Orchestrator 전용 풀버전.
- **`_templates/worker-brief.md` "Worker 행동 규약" 고정 블록** — 워커층 번역형: ②③ 그대로, ①은 가정 명시·표면화(워커는 one-shot이라 사용자 질문 채널 없음), ④는 오케스트레이터 전용.
- **`_templates/worker-result.md` 체크리스트 항목** — "가정·불일치가 Issues/Caveats에 표면화됨".
- **design-basis D8 / system-invariants INV12** — 층별 적용 결정 명문화 + 자가점검.
- **`NOTICE`** — 출처·라이선스 표기 (multica-ai/andrej-karpathy-skills, MIT 선언·LICENSE 파일 부재).

## [1.0.1] - 2026-06-01

모델·추론 정책 표기 정리(문서 patch). 동작 변경 없음.

### Changed
- **모델 식별자 별칭화** (`_shared/routing.md`): claude-main을 버전 문자열(`claude-opus-4-7` 등) 대신 별칭 `opus`로 표기 — 모델이 올라가도 문서 갱신 불필요. codex 예시 일반화, gemini는 `gemini-3.1-pro-low` 핀 유지 + "프록시 업그레이드 시에만 갱신" 노트.
- **claude-main 추론 강도(effort) 명문화**: `effort` 핀 없음 → 세션 `/effort` 상속(현 기본). 고정하려면 frontmatter `effort:`.

### Added
- **design-basis D7**: 모델 식별자 표기 정책(별칭 원칙 / gemini 핀 예외·세부는 D4 정본 / effort 비대칭 근거).

### Verification
- codex-critic adversarial 검수: 치명 0, 권장 3 반영(잔존 핀 제거 포함). INV9/INV10/INV11 PASS, 회귀 없음.

## [1.0.0] - 2026-06-01

첫 버전 태깅. 기존 실사용 시스템을 1.0.0 기준선으로 고정하고, harness(revfactory) 참고 버전 업그레이드를 함께 반영한다.

### Added
- **작업 재진입 프로토콜** (`_shared/orchestrator-rules.md` §3): 콜드세션이 끝난 작업에 다시 들어갈 때 재정박(re-anchor) → 6분기 판단 → 에러 후 진행. `status↔log 불일치`는 다른 분기보다 먼저 적용하는 정규화 단계로 명시.
- **토폴로지 4패턴표** (`_shared/routing.md`): Pipeline / Fan-out·Fan-in / Expert Pool / Producer-Reviewer + Fan-in 규칙.
- **CLAUDE.md** Task Lifecycle에 재진입 프로토콜 포인터.
- **불변식 INV11** (`_shared/system-invariants.md`): 재진입·토폴로지 규정 자동 자가점검(11a/b/c).
- **design-basis D6**: 4패턴 채택 + Supervisor·Hierarchical Delegation 배제 근거.

### Excluded (설계 결정)
- Supervisor·Hierarchical Delegation 패턴: 단일 orchestrator·worker간 무통신·file-as-memory와 충돌하여 미채택 (근거 D6).

### Baseline (1.0.0 시점 핵심 구조)
- 고정 4-worker pool (claude-main / codex-main / codex-critic / gemini), Claude Code 세션 = orchestrator.
- file-as-memory (런타임 상태 0): task / context / log / brief / result.
- 승인 게이트(`workers_approved`), 외부 쓰기 4조건, progressive disclosure(게이트 로드), 권위 우선순위(CLAUDE.md > routing/approval/orchestrator-rules > 매뉴얼).

### Verification
- 배선(INV11a/b/c) PASS · 회귀 없음, 탁상 분기 커버리지, 실전 콜드세션 3/3 PASS, codex-critic adversarial 리뷰 5 ISSUE 반영.

[1.0.1]: https://github.com/netwaif/multi-agent-starter/releases/tag/v1.0.1
[1.0.0]: https://github.com/netwaif/multi-agent-starter/releases/tag/v1.0.0
