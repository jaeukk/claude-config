# MultiAgent — Claude · Codex Orchestration

The current procedure is [`../skills/multiagent/SKILL.md`](../skills/multiagent/SKILL.md); maintainer
notes (tests, the edit-in-a-clone rule) are in [`AGENTS.md`](./AGENTS.md). For a first look,
[`../skills/multiagent/references/picture-book.html`](../skills/multiagent/references/picture-book.html)
shows the procedure in seven pictures; it is for humans, so open it in a browser. The Korean sections
below describe the legacy System A layout and are kept for reference.

Claude Code를 오케스트레이터로 두고 Claude·Codex를 워커로 호출하는 **파일 기반 멀티에이전트 시스템**.

## 핵심 아이디어

- **Orchestrator = Claude Code 세션** (이 폴더 안에서 실행 시 `CLAUDE.md` 자동 적용)
- **Workers** = 외부 모델 호출. 모두 승인 게이트 통과 필요.
  - `claude-main` — [strategist] 기획·설계·아키텍처·전략·디자인 방향·문체 글쓰기
  - `codex-main` — [engineer·computer-use] 대규모 구현·테스트·로컬 검증·브라우저 자동화·이미지 생성
  - `codex-critic` — [reviewer] 산출물 리뷰·비평 (Codex의 주된 역할)

  슬롯→워커 배정의 정본은 `_shared/capability-profile.md`(가변층) — 신모델 출시 시 프로필만 갱신.
- **Memory = filesystem.** 런타임 상태 없음. 모든 결정·승인·검증이 파일로 남는다.

## Accounts — team first, private fallback (2026-09-16)

Two Claude logins: private (`~/.claude`, Max, Fable; the conductor, unrestricted) and team
(`~/.claude-team`, KAIST, no Fable). A Claude backend may carry `account` (`private`|`team`) and
`config_dir`. A team backend leads wherever nothing argues against it: `claude-core-team`
heads `implementer` (a native spawn skips account-bound backends and gets `claude-core`),
`claude-fast-team` heads `bulk_worker`, and `claude-mid-team` precedes `claude-mid` in
`verifier`. `runner` keeps `codex-fast` first (recorded benchmark: equal accuracy at 26x fewer
tokens, and a Claude CLI runner cannot run commands); team is its Claude-side second choice.
`critic` (claude-ceiling, Fable) stays private: Fable does not exist on the team plan.
`dispatch-worker` probes the team quota once per dispatch (`engine/accounts.py`, usage API,
cutoff 95%, 60 s cache), skips team candidates only when that probe says exhausted (an
unreadable probe routes to team anyway), launches the CLI worker with `CLAUDE_CONFIG_DIR`, and
retries exactly once on private after a positively classified rate limit (HTTP 429), never in
write mode. Every attempt is an `events.ndjson` record (`type: worker_attempt`, account, model,
reason, exit, and the usage or token count the CLI reports). Native subagents never use team
(they run under the session login). Manual use:
`claude-worker -p "$(cat brief.md)"` (team first, private backup) or `claude-team` (forced).
`bindings.yaml` is JSON-parsed, so this rule lives here, not as a comment there.

## Windows and WSL

The workspace is shared through the Windows-mounted project path. The Node launchers in
`engine/adapters/` select the installed Windows Python 3.14 and `codex.cmd` on Windows, or
`python3` and native `codex` inside WSL. The policy engine itself requires only the standard
library and runs on WSL's `python3`. Native WSL Codex is therefore a worker host, not a
Windows-command compatibility shim.

## 폴더 구조

```
<설치한-폴더>/
├── CLAUDE.md              # 운영 규칙 전문 (이 폴더 안에서 claude 실행 시만 적용)
├── _shared/
│   ├── routing.md             # worker 선택 decision tree + 호출 명령
│   ├── approval-policy.md     # 승인 게이트 정책 (claude-main 포함)
│   ├── orchestrator-rules.md  # 세션 시작 시 자체 점검 규칙
│   └── learnings.md           # 시스템 일반 재사용 교훈 (추적·공개, append-only)
├── _templates/
│   ├── task.md            # status, goal, constraints, planned_workers, workers_approved
│   ├── context.md         # 현재 스냅샷 ≤ 1500자 / 300단어
│   ├── worker-brief.md    # ≤ 1200자 / 240단어, target_repo + write_scope
│   ├── worker-result.md   # Verification Checklist 포함
│   ├── log.md             # append-only 이력
│   └── task-folder.md     # 새 작업 폴더 생성 가이드
└── tasks/                 # 작업별 폴더 (동적 생성)
    └── <task-name>/
        ├── task.md
        ├── context.md
        ├── log.md
        ├── sources/       # 원본 자료 (선택)
        ├── workers/<role>/
        │   ├── brief.md
        │   └── result.md
        └── artifacts/     # 산출물 원본 (선택)
```

> `_local/` (git 추적 안 함, clone 시 빈 폴더): 작성자의 **프로젝트 특화** 교훈
> (`_local/learnings.md`)이 여기 쌓인다. 공개 starter에는 **시스템 일반** 교훈만
> `_shared/learnings.md`로 배포된다. 분류 규칙은 `_shared/learnings.md` 헤더 참조.

## 사용 시작

```bash
cd <설치한-폴더>
claude
```

자연어로 새 작업 요청:
> "새 작업 만들어줘. 목표는 ○○이고 ○○ worker가 필요할 것 같아."

Orchestrator가 `_templates/task-folder.md` 가이드에 따라 작업 폴더 생성 → worker 승인 요청 → 진행.

## 모니터링 (선택) — mat

작업 진행을 터미널에서 지켜보고 싶다면 **[mat](https://github.com/netwaif/mat)** (MultiAgent Tracker)를 함께 쓴다.
한 작업의 워커 상태(대기·실행 중·완료·에러)·goal·로그를 한 화면에서 본다.
시스템을 **읽기만** 한다 — 작업 생성·승인·워커 호출은 하지 않으므로, 켜두거나 꺼도 진행에 영향이 없다.

```bash
brew install netwaif/tap/mat
MAT_ROOT=<설치한-폴더> mat
```

설치·키 조작 등 자세한 내용은 [mat 저장소](https://github.com/netwaif/mat) 참고.

> ⚠️ mat에서 워커 한 줄 목적이 ` ```yaml `로 보이면 **알려진 경미 이슈**(KI-1)다.
> 시스템·진행에는 영향 없다. [`KNOWN_ISSUES.md`](./KNOWN_ISSUES.md) 참고.

## 알려진 이슈

해결·보류 중인 알려진 결함은 [`KNOWN_ISSUES.md`](./KNOWN_ISSUES.md)에 추적한다.

## 핵심 원칙

| 원칙 | 강제 방식 |
|------|---------|
| 모든 worker 호출 전 승인 | `task.md`의 `workers_approved` 필드 |
| 측정 가능한 컨텍스트 한도 | `wc -m` / `wc -w`로 검증 |
| append-only 로그 | `log.md` 수정·삭제 금지 |
| 최소 worker set | `routing.md` decision tree로 강제 |
| codex-main 외부 repo 쓰기 4-조건 | `target_repo` + `write_scope` + 승인 + log [APPROVAL] |

System A's full rules are archived at [`_archive/system-a/AGENTS.md`](./_archive/system-a/AGENTS.md).

## 라이선스

개인 사용 및 학습 목적.
