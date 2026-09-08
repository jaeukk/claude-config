# Orchestrator Rules

Rules for a MultiAgent conductor using a declared conductor adapter. Check these requirements at session startup; report violations and stop.

---

## 1. Orchestrator 실행 환경

Interactive sessions only. Orca-managed terminals in git worktrees are permitted. Background or headless conductor sessions are forbidden. Exactly one conductor terminal owns each task. All participants use the same explicit task directory for persistent task records. The conductor host must be declared in `policy/routing.yaml` under `conductor_adapters` (`claude-code` or `codex`). Check the session context at startup:

- 시스템 프롬프트에 `# Background Session` 블록이 보이거나
- `$CLAUDE_JOB_DIR` 환경변수가 설정돼 있으면

If either background indicator above is present, stop and request an interactive session. An Orca worktree terminal qualifies only when the single conductor and all participants retain access to the same explicit task directory; worktree placement does not select or relocate a task root.

---

## 2. 시스템 수정·검증 프로토콜

**적용 조건 (게이트)**: 이번 작업이 시스템 파일 — `CLAUDE.md`·`_shared/*`·`_templates/*`·외부 매뉴얼(`<매뉴얼-경로>/`) — 을 **수정하거나 검증**하는 작업일 때만 이 절을 적용한다. 일반 작업에서는 아래 파일들을 읽지 않는다 (progressive disclosure — 상시 로드 금지).

**Working directory**: Perform system maintenance from the installation directory using its bound conductor adapter (`claude-code` or `codex`). Load the applicable `CLAUDE.md` or matching `AGENTS.md`, read design-basis, and complete the checks below. Work performed outside that instruction context must stop for review before continuing. Adapter eligibility does not grant additional write permissions or bypass the current enforcement and conductor-handoff requirements in `docs/architecture.md`.

**절차**:
1. `_shared/design-basis.md` 를 읽는다 — 개념↔규칙 매핑·권위 우선순위·기존 결정(D*). GitHub 레퍼런스부터 재분석하지 말 것. design-basis로 충분.
2. 수정한다 (권위 우선순위 준수: CLAUDE.md > routing/approval/orchestrator-rules > 매뉴얼).
3. `_shared/system-invariants.md` 의 자가 점검 스크립트를 실행한다.
4. 통과 시에만 커밋. 깨지면 고치거나, 의도된 변경이면 `design-basis.md`의 결정(D*)과 `system-invariants.md`를 함께 갱신한 뒤 커밋.

**전면 재감사 조건**: 새 외부 개념·레퍼런스 도입, worker pool 구성·역할 변경, 불변식으로 표현 불가한 구조 변경일 때만 새 `tasks/<task>/`로 codex-critic 포함 재점검. 그 외 일반 수정은 위 4단계로 충분 — 매번 바닥부터 분석하지 않는다.

---

## 3. 작업 재진입 프로토콜 (기존 작업에 다시 들어갈 때)

이미 `tasks/<task>/`가 있는 작업을 다시 만질 때(특히 맥락 0인 새 세션) 적용. 끝난 작업이라도 콜드세션이 맨손으로 시작하지 않게 한다.

**1단계 — 재정박(re-anchor, 필수)**: 어떤 액션(편집·worker 호출) 전에도 먼저 읽는다 — `task.md`(goal·**status**·workers_approved) → `context.md`(현재 스냅샷) → `log.md` 최근 항목(특히 마지막 `[WORKER_CALL]`/`[VERIFICATION]`) → 관련 `workers/<role>/brief.md`·`result.md`. 읽기 전 행동 금지.

**2단계 — 분기 판단** (result.md 유무만으로 판단하지 말 것 — status·log·brief를 함께 본다):
- **초기 실행** — brief·result 모두 없고 status `pending` → 정상 라이프사이클(CLAUDE.md "Task Lifecycle")
- **응답 대기/지연** — status `waiting_<role>`이거나 log에 `[WORKER_CALL]`만 있고 result.md 없음 → 초기 실행 아님. worker hang인지 늦게 도착할 응답인지 먼저 확인. hang 판정 시 3단계로
- **부분 재실행** — 특정 worker result만 미흡/검증 미통과 → 그 worker만 재호출(routing.md "Worker 추가 조건"). 다른 worker 결과 보존
- **기존 결과 개선** — 기존 result.md를 입력으로 개선. 이전 result.md는 덮지 말고 `result-fix.md` 등으로 버전 보존하고, **현재 채택(authoritative) result 경로를 `context.md`에 명시**(어느 게 최신인지 모호 금지)
- **새 입력** — 입력이 바뀌었으면 이전 산출물 보존하고 새 result로. 범위가 다르면 새 작업 폴더(단 아래 분리 게이트 적용). **`target_repo`/`write_scope`가 바뀌면 기존 승인은 무효 — 새 승인 없이는 외부 쓰기 금지(artifacts diff로 제한)**
- **새 작업 폴더 생성 게이트 (분리·핸드오프·후속 단계) — 강제**: 기존 작업의 후속·핸드오프·하위 단계를 별도 폴더(경로 불문)로 분리하려면 먼저 사용자에게 폴더 구조(분리 여부·폴더명)를 확인·승인받는다. done 작업이라도 자동 분리 금지 — 사용자가 폴더를 봐야 알게 되는 우연 발견은 추적 실패다. 분리가 일어나면(사용자가 먼저 분리를 지시했어도) 항상 연결고리를 채운다: ① 새 `task.md`에 `parent:` ② 새 `context.md`에 부모의 authoritative 산출물·log 핵심구간을 '필독 입력'으로 경로만(inline 금지) 명시 ③ 메모리 인덱스를 쓰는 환경이면 거기에 부모↔자식 포인터 1줄. 확인 절차는 사용자가 분리를 요청했으면 면제되나 ①~③ 연결고리는 면제 안 됨. 예외: 기존 작업과 독립된 신규 작업이라고 명시한 경우만 정상 라이프사이클.
- **status↔log 불일치 (다른 분기보다 먼저 적용)** — 위 분기들은 status를 신뢰해 판단하므로, 불일치 검사를 **가장 먼저** 한다. 불일치면 log(append-only 정본)를 신뢰해 status를 실제에 맞게 정정하고 `[DECISION]` 기록한 뒤, **정정된 status로 위 분기를 다시 적용**한다 (불일치는 단독 종착 분기가 아니라 정규화 단계)

**3단계 — 에러 후 진행(worker 실패·hang·상충)**:
- 실패/타임아웃 → **1회 재시도**. 재실패 시 진행을 통째로 멈추지 말고 누락을 `result.md`·`log.md` `[ERROR]`에 명시하고 가능한 부분만 진행
- 결과 상충(병렬·재실행 간) → 삭제 금지. 양쪽 출처 병기, 권위 우선순위/사실검증으로 해소, `log.md` `[DECISION]` 기록 (routing.md Fan-in 규칙과 동일)

재진입 시에도 승인 게이트·외부 쓰기 4조건은 그대로 유효. 재개 시 status를 `in_progress`로 되돌린다.
