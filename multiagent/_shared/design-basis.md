# Design Basis — 왜 이 시스템이 이렇게 생겼나

> **로드 정책**: 이 파일은 평소 작업에서 읽지 않는다. **시스템 파일(_shared/·_templates/·CLAUDE.md·외부 매뉴얼)을 수정·검증하는 작업일 때만** orchestrator가 읽는다. (progressive disclosure — `orchestrator-rules.md` §2 프로토콜 참조)
> 목적: 시스템을 고치거나 검증할 때 GitHub 레퍼런스부터 바닥 재분석하지 않기 위함. 결정의 "왜"를 여기 박아둔다.

## 0. 출처

- 개념 출처: https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering (MIT)
- 4원칙(운영 원칙) 출처: https://github.com/multica-ai/andrej-karpathy-skills (MIT 선언, LICENSE 파일 부재 — 표기는 `NOTICE` 참조)
- 코드 starter: https://github.com/netwaif/multi-agent-starter
- 1차 전면 점검: `tasks/manual-final-review/` (2026-05-15) — review-report.md / sources/github-reference-digest.md 에 상세

## 1. 핵심 개념 → 시스템 규칙 매핑 (재분석 금지, 이 표를 신뢰)

| 레퍼런스 개념 | 시스템에서 구현된 규칙 | 건드릴 때 주의 |
|---|---|---|
| 컨텍스트 = 유한 attention budget | context.md ≤1500자, brief ≤1200자 | 한도 숫자는 근거 있는 값. 바꾸려면 design 근거부터 |
| Progressive disclosure | sources/ 경로참조, brief 최소화, design-basis 게이트 로드 | 새 상시로드 자산 추가 금지 |
| Filesystem = 오버플로 메모리 | task/context/log/brief/result, 런타임 상태 0 | "메모리=파일" 깨는 변경 금지 |
| Lost-in-the-middle | 핵심 제약은 앞, 금지는 끝 (매뉴얼 §3/§6) | 긴 규칙 추가 시 위치 고려 |
| Append-only + provenance | log.md append-only, 태그 6종 | 태그 집합·append-only 불변 |
| 재사용 메모리 기준 | learnings.md (재사용 교훈만) | 일회성·작업특화 적재 금지 |
| Orchestrator 패턴 / 컨텍스트 격리 | Orchestrator=세션, worker별 깨끗한 brief | brief에 타작업 찌꺼기 금지 |
| Telephone game (paraphrase 손실) | worker 원문을 result.md 보존 | 요약본만 저장 금지 |
| Output validation (never trust upstream) | result.md Verification Checklist, 검증 전 전달 금지 | 리뷰어(gemini/codex) 출력도 사실검증 후 채택 |
| Consensus: 다수결 금지, adversarial | codex-critic의 adversarial 리뷰 | critic을 단순 확인용으로 격하 금지 |
| 토큰경제 ~15x | 승인 게이트 + 최소 worker set | "전 worker 기본 호출" 금지 |
| Latent briefing (task-guided 압축) | brief = 그 worker의 그 작업용으로 재구성 (텍스트 근사, KV 불가) | "앞 작업 요약 그대로 전달" 금지 |
| Context degradation: Clash | 문서 충돌 시 권위 우선순위로 해소 | 권위순위(§3) 유지 |
| project-development: single→multi 승격 | routing.md 최소 set, 판단 어려우면 claude-main부터 | 기본 단일, 필요 시만 확장 |
| Fan-out/Fan-in + 작업 재진입 | routing.md 토폴로지표·Fan-in 규칙, orchestrator-rules §3 | 병렬 통합·재진입 분기는 기존 append-only+provenance·never-trust-upstream 재사용. 새 원칙 아님 |

## 2. 권위 우선순위 (Context Clash 해소 규칙)

`CLAUDE.md` > `_shared/routing.md`·`approval-policy.md`·`orchestrator-rules.md` > 외부 매뉴얼(multi-agent-manual.txt).
충돌 발견 시 낮은 쪽을 높은 쪽에 맞추고 log.md에 남긴다. 매뉴얼은 항상 시스템 권위문서에 종속.

## 3. 이미 내린 결정 (재논의 금지, 뒤집으려면 근거 갱신)

- **D1 (B2) write_scope 값 집합** = `none | tasks-only | "패턴"`. `tasks-only`=codex-main 기본(tasks/<task>/ 내부만). CLAUDE.md가 정식 정의처. routing.md·_templates·매뉴얼 동일해야 함. (2026-05-15 R1)
- **D2 (B7) codex-critic 선행조건** = "리뷰 대상 산출물 경로 존재 — 보통 claude-main result.md, 또는 brief에 명시된 기존 코드·문서·소스". claude-main 전용 아님. (2026-05-15 R2)
- **D3 (R5) context.md 구조** = 4섹션 유지. 레퍼런스 5단(Intent/Files/Decisions/State/Next)은 *압축/핸드오프 체크리스트*지 context.md 템플릿 아님. `Files Modified/Decisions Made` 헤딩 도입 금지(히스토리 변질 → log.md 역할 침범). codex-critic 검증 완료. (2026-05-15)
- **D4** gemini 백엔드 = **Antigravity `agy` CLI**(`_shared/backends.json` 정본, 디스패처 `_shared/adapters/call_worker.sh`). 기본 모델 = `gemini-3.1-pro-high`, 빠른 경로 = `gemini-3-flash`/`pro-low`, 폴백 = `api`(`adapters/gemini_api.sh`). 옛 단일 브리지 `mcp__gemini-pro__*`(antigravity-claude-proxy 의존)·CLI 래퍼 `mcp__gemini__*`는 **폐기**(잔존 활성 참조는 호출 실패). `pro-high`를 기본·폴백에서 제외했던 사유(로컬 프록시 `400 INVALID_ARGUMENT` 재현, learnings 2026-05-15)는 **agy 백엔드엔 비해당** — agy로 pro-high 정상 실증(2026-06-02). agy 모델은 전역·계정단위(`/model`)라 per-call 핀 불가 → gemini 전용 전역을 pro-high로 운용. 폴백 모델의 시스템 사실 주장은 권위문서로 교차검증 후 채택. (근거 갱신 2026-06-02: tasks/v2-harness-adapter-build/ — agy 백엔드 전환 spike+3자검수. 이전: 2026-05-19~20 단일 mcp__gemini-pro__ 브리지·pro-low 기본·pro-high 제외, 프록시 의존)
- **D5** Interactive sessions only. Orca-managed terminals in git worktrees are permitted. Background or headless conductor sessions are forbidden. Exactly one conductor terminal owns each task. All participants use the same explicit task directory for persistent task records. See orchestrator-rules.md section 1.
- **D6** 작업 재진입 프로토콜(orchestrator-rules §3) + 토폴로지 4패턴(routing.md) 채택, Supervisor·Hierarchical Delegation **배제**. 배제 대상은 *개념*이 아니라 *추가 계층*임에 유의 — 기존 단일 orchestrator를 Supervisor로 재명명하는 게 아니라, 그 위에 (Supervisor) 별도 long-lived 조정자 worker/런타임 동적분배 계층, (Hierarchical) worker가 worker를 부르는 재귀 위임 트리를 추가하는 것을 배제한다. 근거: (a) 단일 orchestrator, (b) worker간 무통신(전부 orchestrator 경유+승인 게이트), (c) file-as-memory(런타임 0). 추가 계층은 (b)(c)와 승인·비용·감사를 무너뜨림. 재진입 프로토콜은 콜드세션 재정박 공백(사용자 보고 통증)을 메움. 부분재실행·에러처리·Fan-in 충돌해소는 모두 기존 불변식(append-only+provenance, never-trust-upstream, 최소 worker set)의 재배치 — 새 원칙 아님. 출처: harness(revfactory) 6패턴 중 4개 선별, tasks/harness-vup-reentry/ (codex-critic 검토 반영). 매뉴얼 동기화는 후속(manual sync pending). (2026-06-01)
- **D7** 모델 식별자 표기 정책 = **휘발성 높은 식별자는 별칭으로, 안정적인 핀만 핀으로**. 근거: "현재 모델 식별자"는 시스템이 아니라 *환경*이 소유하는 사실이며 워커마다 변동성이 다르다. (a) **claude-main** = 별칭 `opus`(Anthropic이 최신 Opus로 해석) — 버전 문자열 핀 금지. (b) **codex** = `~/.codex/config.toml` 기본값이 정본 — repo에 버전 핀 금지(routing은 "예시"로만). (c) **gemini** = 백엔드 `agy` CLI, 기본 `gemini-3.1-pro-high` **핀 유지** — agy 모델은 전역·계정단위(`/model`)라 per-call 핀 불가하므로 backends.json에 명시 핀이 정본(별칭화 부적합). 모델 전환은 드문·의도적 이벤트. **gemini 세부 정책(백엔드·기본·폴백)의 정본은 D4** — D7은 표기 원칙만 다룬다. (옛 `pro-low`+프록시 핀은 D4 마이그레이션으로 폐기, 2026-06-02) **추론 강도(effort)**: claude-main은 frontmatter `effort: xhigh` **핀**(상속 끔) — 2026-07-28 변경 전에는 핀 없이 세션 `/effort` 상속이 기본이었다. codex는 config.toml에 `model_reasoning_effort: high`가 **지속 기본값**으로 박혀 있음(운영자 환경 기준 고정적이나 config·profile·brief·MCP 파라미터로 바뀔 수 있음 — "결정적"은 아님). 출처: tasks/model-policy-cleanup/ (codex-critic 검수 반영). (2026-06-01, effort 절 갱신 2026-07-28)

- **D10 정책층(System B) 모델 식별자는 핀** = D7의 별칭 원칙은 **System A**(`_shared/backends.json`, agent frontmatter) 소관이고, **System B**(`policy/backends.yaml`)는 **명시 핀**을 쓴다. 근거: (a) `conductor` 바인딩은 `session_assertion`이라 핀이 `~/.claude/settings.json`의 실제 세션 모델과 **대조 가능**해야 한다 — 별칭은 이 대조를 불가능하게 만든다. (b) 레지스트리의 존재 이유는 **구분되는 티어를 명명**하는 것인데, 별칭을 쓰면 `claude-frontier`와 `claude-core`가 둘 다 `opus`로 읽혀 티어 구분이 소멸한다. (c) codex 백엔드는 `dispatch_codex`가 `--model`로 **실제 사용**하므로 핀이 load-bearing이다(D7b의 "repo에 핀 금지"는 System A 한정). 반대급부: 신모델 출시 시 `policy/backends.yaml`을 손봐야 하는 개정 부채 — 파일 1개·6줄이므로 수용. 주의: Claude 백엔드의 `model:` 값은 **선언적**이다(엔진은 codex에만 모델 문자열을 전달) → 모델 재배정 시 세션 레버(`multiagent/.claude/settings.json`, `install_wsl_orchestration.js`, agent frontmatter)를 함께 고쳐야 실제로 바뀐다. (2026-07-28, tasks 없음 — orchestrator 직접 수정)

- **D8 카파시 4원칙 층별 적용** = 오케스트레이터 지침(CLAUDE.md "운영 원칙 (Operating Principles)" 섹션) 풀버전 verbatim 차용(도입 tradeoff·말미 성공지표 포함) / 워커층 유일 정본은 `_templates/worker-brief.md`의 "Worker 행동 규약" 고정 블록 — ②단순함·③외과수술식 그대로 + ①추측전질문은 **번역형**(워커는 one-shot/headless라 사용자 질문 채널 없음 → 가정 명시·불확실/불일치를 result.md Issues/Caveats에 표면화) / ④목표기반 loop은 오케스트레이터 전용(Verification Checklist 루프와 결합). 워커 brief·agent 정의에 "사용자에게 질문" 지시 금지, agent 정의에 규약 중복 금지(brief가 모든 워커에 닿는 유일 운반체 — call_worker.sh가 brief를 통째로 전달). 기존 D와 무충돌·동방향 보강(②=최소 worker set·토큰경제, ③=write_scope 4조건, ④=never-trust-upstream 검증, D6 구조 불변). 출처: multica-ai/andrej-karpathy-skills — MIT는 README·plugin.json 선언 기준이며 상류에 LICENSE 파일 없음(2026-06-10 확인), 재배포 표기는 `NOTICE` 정본. (2026-06-10, tasks/karpathy-wiki-upgrade/)

- **D9 라우팅 2층 분리** = `routing.md`(안정층: 작업 유형→능력 슬롯 strategist·engineer·computer-use·reviewer·multimodal)와 `_shared/capability-profile.md`(가변층: 슬롯→담당 배정, 근거·날짜 필수, 이력 append-only). 트리의 담당명 병기는 편의 사본 — 프로필이 정본. 근거: 모델별 강점 우열은 신모델 출시마다 바뀌는 *환경 소유 사실*(D7 동방향)이라 시스템 파일에 구우면 세대마다 개정 부채가 된다. 초기 배정 근거 = 2026-07-13 외부 리뷰 10건 종합 판정(Anthropic vs OpenAI 최신 플래그십): 설계·UI/UX 디자인·전략·글쓰기 = Claude 우위, 대규모 구현·테스트·브라우저 조작·비용·속도·토큰 효율 = GPT 우위로 수렴 — computer-use 슬롯 신설 동근거. 갱신은 판정 자료 확보 시 프로필만(절차는 프로필 파일이 정본). 검증: validate C1(프로필 존재)+C5b(routing→profile 참조, 슬롯 5종). (2026-07-13)

- **D11 agy = System B 1급 백엔드 + per-call 모델 핀** = (a) `policy/backends.yaml`에 `agy-multimodal`(`gemini-3.1-pro-high`)·`agy-fast`(2026-07-31 step 1 실측 후 `gemini-3.6-flash-low`로 재핀 — 최초 등재값은 `-medium`)를 **family `gemini`**로 등재하고 `bulk_worker`·`runner` 풀 및 `critic`·`verifier`의 3순위 후보에 편입. 근거: 2-family 구성에서는 critic/verifier의 different-family 후보가 각 1개뿐이라 **해당 벤더 장애 = 독립 검증 정지**라는 단일 실패점이 있다. 3번째 family가 이 구조적 공백을 없앤다. **단 자동 failover는 아니다** — `resolve_binding`은 가용성을 탐지하지 않고 첫 적격 후보를 반환하므로 3순위 gemini는 정상 경로에서 결코 선택되지 않는다. 벤더 장애 시 Orchestrator가 **명시적으로 선택**해야 하는 후보이며, 그 선택지가 존재한다는 것이 이 변경의 효용이다(codex-critic 2026-07-31 지적 반영). 또한 System B에는 agy 디스패치 경로가 없다(`dispatch_codex` 단독) — `agy-*`의 capability 선언은 System A `call_worker.sh` 경유 실행을 전제한 것이지 System B가 자체 호출할 수 있다는 뜻이 아니다. 이를 위해 `engine/policy_engine.py`의 `bulk_families != {"claude","codex"}` **정확일치**를 **부분집합**(`{"claude","codex"} <= bulk_families`)으로 완화 — Claude·Codex 필수 요구는 그대로 두고 추가 family만 허용한다(요구 완화 아님). `resolve --author-family` choices에 `gemini` 추가. (b) **D7(c) 근거 정정**: agy 1.1.8은 `--model`·`--effort low|medium|high`를 **인자로** 받으므로 "agy 모델은 전역이라 per-call 핀 불가"는 더 이상 성립하지 않는다. `call_worker.sh`는 `@brief`/`@brief_content` 외 인자를 그대로 통과시키므로 **코드 변경 없이** `args_template`에 `--model <id>`를 넣어 핀한다. D7(c)의 *결론*(backends.json 명시 핀이 정본)은 유지되고 *근거*만 "전역이라 불가피"에서 "명시적 선택"으로 바뀐다 — 결론 불변이므로 D7 자체는 재논의 대상 아님. (c) **agy 경유 비-Gemini 모델 등재 금지**: agy는 `claude-sonnet-4-6`·`claude-opus-4-6-thinking`·`gpt-oss-120b-medium`도 노출하나, 이를 등재하면 `family: gemini`가 실제 모델 벤더와 어긋나 critic/verifier 독립성이 **조용히**(에러 없이) 무너진다. 엔진이 `host == "agy"`이면서 `model`이 `gemini-` 접두가 아닌 항목을 error로 차단 — 음성 테스트로 확인. 검증: `policy_engine.py validate-policy`·`self-test` + INV13. 주의: vault 사본(`20_Notes/_shared/`)은 System A 전용 미러라 D10·D11·INV13을 담지 않는다. (2026-07-31, tasks/agy-integration/)

- **D12 conductor 자격 = 호스트 이름이 아니라 디스패치 도달성** = `task.schema.json`이 conductor
  host/backend를 상수로 고정하던 방식을 폐기하고, **계산되는 3조건**으로 대체한다: (a) 해당 backend가
  `bindings.yaml`의 `conductor` 후보일 것, (b) 그 host에 `conductor_adapters` 항목이 있을 것,
  (c) 그 항목의 `dispatch_hosts`가 **모든 author family에 대해** 독립 critic·verifier에 도달할 것.
  `validate_policy`가 (c)를 전 후보에 대해 검사하고 `resolve_binding`은 어댑터가 호출할 수 없는 후보를
  건너뛴다. 근거: 기존 상수의 정당화("Codex child API는 Codex family만 스폰 가능")는 **하나의 디스패치
  수단**의 한계였지 호스트의 한계가 아니다 — `claude`·`codex`·`agy`는 모두 CLI이므로 Codex conductor도
  서브프로세스로 Claude critic을 호출할 수 있다(실증: codex/codex-conductor 계약으로 Claude worker
  dispatch, exit 0). 따라서 `codex-conductor` 백엔드를 등재하고 conductor 후보에 편입한다.
  `dispatch_hosts`는 **필수·fail-closed**(누락 = 아무것도 디스패치 못 함)이며, `WORKER_CLI`에 빌더가
  없는 host를 선언하면 validation error다 — "도달 가능"이 조용히 의미를 잃는 것을 막는 장치.
  **여기서 "도달 가능"은 정적 도달성**이다 — 빌더와 설정이 존재한다는 뜻이지 실행 가능성·인증·쿼터·
  헬스를 뜻하지 않는다. `resolve_binding`은 여전히 가용성을 탐지하지 않는다.
  **D10·D11 부분 정정**: (i) D10의 "엔진은 codex에만 모델 문자열을 전달"은 `dispatch-worker` CLI 경로에
  한해 폐기된다 — 이 경로는 claude에도 `--model`·`--effort`를 전달하므로 해당 핀이 load-bearing이다.
  단 **네이티브 Task 경로로 스폰된 Claude subagent는 여전히 선언적**이며 세션·frontmatter가 정본이다
  (D10의 세션 레버 3곳 경고는 그 경로에 대해 그대로 유효).
  (ii) D11의 "System B에는 agy 디스패치 경로가 없다"는 **부분 폐기**다 — `WORKER_CLI`에 `agy` 빌더는
  추가했으나(prompt는 argv, 일회용 cwd, `--add-dir` 없음), **어떤 어댑터의 `dispatch_hosts`에도 넣지
  않았다**. 즉 빌더는 존재하되 엔진이 선택할 수 없다. 보류 사유 2가지: (1) **격리가 입증되지 않았다** —
  `--sandbox`는 터미널 제한이지 파일시스템 경계가 아니어서 절대경로 쓰기를 막지 못하며, 그래서 System A가
  쓰는 `--dangerously-skip-permissions`를 여기서는 **의도적으로 뺐다**. (2) 이 경로로 **완료를 한 번도
  관측하지 못했다**(agy 쿼터 소진). 해제 조건은 이 둘의 충족이며, 그때 argv 길이 한계(Windows 32,767자)도
  함께 처리해야 한다. D11의 *결론*(3번째 family는 자동 failover가 아니라 명시적 선택지)은 유지되지만,
  현재 그 선택지는 System A `call_worker.sh` 경유로만 실재한다.
  **미해결(의도적)**: Codex에는 PreToolUse 어댑터가 없어 **강제가 없다** — 계약은 검증될 뿐 집행되지
  않으므로 문서·프롬프트에서 "policy-enforced"가 아니라 **"policy-validated"**로 쓴다. Claude Code의
  훅도 Bash로 워커 CLI를 직접 띄우면 우회되며, 이를 막는 정규식은 **휴리스틱**(실수 방지용)이지 경계가
  아니다. (2026-08-25, codex-critic Sol high 2라운드 감사 반영 — 1라운드 9건·2라운드 7건)

- **D13 세어야 하는 수는 세어지는 쪽이 소유한다** = (a) `dispatch.active_workers`는 **제한 대상인
  conductor 자신이 쓰는 값**이라 0으로 두면 fan-out 상한이 무력화됐다. `dispatch-worker`는 이제
  **lease에 슬롯을 claim/release**한다(`claim_worker_slot`·`release_worker_slot`, `finally`에서 반환). 카운터 변경은 **lock 파일(O_EXCL) 아래 read-modify-write**다 — fan-out은 곧 여러 `dispatch-worker` 프로세스가 한 lease를 동시에 고치는 상황이라, 잠금 없이는 증가분이 유실되어 상한을 넘긴다(40스레드 경쟁 실측: limit 3 → 정확히 3건 승인). 또한 claim은 **lease 세대(`acquired_at`)에 묶이고**, 워커 실행 중에는 **heartbeat 스레드가 lease를 갱신**한다(기본 TTL 300초는 실제 워커보다 짧다 — 이 감사 자체가 그랬다). release는 만료된 lease에서도 허용하되(슬롯 누수 방지) **세대가 다르면 거부하고 stderr로 경고**한다.
  lease는 (협조적 참여자 전제 하에) 단일 소유자가 관리되는 유일한 파일이므로 **위조되면 안 되는 수**가 있을 자리다. 부수 효과로
  실제 dispatch는 **살아있는 자기 소유 lease를 요구**한다(없으면 거부) — 절차 3(lease)→4(dispatch) 순서를
  엔진이 강제하게 됐다. 네이티브 스폰(Claude `Task`·Codex `spawn_agent`)은 엔진을 거치지 않으므로 여전히
  계약값에 의존한다 — **두 카운터가 공존**하며 신뢰도가 다르다. 단 **상한은 하나**이며 **양방향**이다:
  CLI claim은 계약값을 `reserved`로 charge하고, 훅의 네이티브 검사는 lease의 보유 슬롯을 더한다
  (한쪽만 하면 각자 limit만큼 떠서 총량이 2배가 된다 — codex 지적).
  **위협 모델을 명시한다**: 이 카운터가 막는 것은 **협조적인 conductor 1명의 사고성 fan-out**이지
  보안 경계가 아니다. dispatcher가 SIGKILL되면 자식은 살아남고 슬롯은 물린 채 남으며, lease 만료 후에는
  카운트만 사라지고 고아 워커가 남을 수 있다. 네이티브 워커는 conductor가 신고해야만 세어진다.
  크래시·적대적 참여자까지 견뎌야 한다면 JSON 카운터가 아니라 supervisor가 필요하다 — 이 문단보다
  강한 약속을 암시하는 장치를 여기에 더 붙이지 말 것.
  (b) `dispatch-worker`가 `--role`로 인가하면서 훅은 `dispatch.current_role`을 읽어 **두 집행 경로가 서로
  다른 역할을 볼 수 있었다** → 불일치 시 거부한다.
  (c) `policy/task.schema.json`은 **어떤 코드도 로드하지 않았다**(정본은 `validate_task()`이고 부분집합만
  검사 — 예: `approvals.user` 미검사). "machine-readable policy"라는 표기가 실제보다 과대였으므로
  **`docs/task-contract.schema.json`으로 이동**하고 `$comment`에 설명적 문서임을 박아 넣었다. 경로 자체가
  지위를 드러내게 하는 것이 목적 — 스키마를 고쳐도 동작은 안 바뀐다. 강제로 승격하는 선택지도 있었으나
  중복 정본 2개를 만드는 비용이 더 크다고 판단했다.
  근거: codex 자가 적격성 점검(2026-08-25)에서 (a)(b)(c) 지적. (2026-08-25)

- **D14 agy(gemini) 전면 비활성 — D11 철회, D4·INV9 폐기** = 사용자 지시로 agy 백엔드를 껐다.
  제거: `_shared/backends.json`의 `gemini`·`gemini-reader` 워커(System A), `policy/bindings.yaml`의
  `agy-multimodal`·`agy-fast` 후보(System B — critic·verifier 3순위, bulk_worker·runner 풀).
  **남긴 것**: `policy/backends.yaml`의 `agy-*` 레지스트리 항목, 엔진의 `_agy_cli` 빌더,
  `host: agy` → `family: gemini`·`gemini-` 모델 강제 검사. 어떤 바인딩·`dispatch_hosts`도 참조하지
  않으므로 도달 불가이며, 복원 시 재작성을 피하려 보존한다(삭제는 별도 결정 사항).
  **대가를 명시한다**: (a) D11이 없애려던 단일 실패점이 **되돌아온다** — 2-family 구성에서
  critic·verifier의 different-family 후보가 다시 각 1개뿐이라, 한 벤더 장애가 독립 검증을 정지시킨다.
  (b) capability-profile의 `document-reading`·`multimodal` 슬롯 담당이 비고, codex-main → claude-main이
  대신한다. 2026-07-31에 기록한 배정 근거(agy는 별도 계정이라 Claude·Codex 주간 한도를 소모하지 않음)가
  사라지므로 **문서읽기가 이제 Claude·Codex 쿼터를 쓴다**. (c) `gemini_raw_build.py` 경로(섹션 단위
  문서→노트 배치)도 agy를 직접 호출하므로 함께 멈춘다.
  **INV9 폐기**: "backends.json의 gemini 백엔드가 agy CLI·pro-high" 불변식은 이제 거짓이며, 자기점검
  스크립트가 실패한다. 반대 방향(agy/gemini 워커가 **없어야** 함)으로 뒤집었다. INV13(agy 항목의
  family 정합성)은 레지스트리 항목을 남겼으므로 그대로 유효하다.
  **복원 조건**: bindings 후보 + `dispatch_hosts` + backends.json 워커를 **함께** 복원해야 하며, 그 전에
  `_agy_cli` docstring의 두 전제(격리 입증·완료 관측)와 argv 길이 한계(Windows 32,767자)를 처리할 것.
  절반만 되살리면 D13 이전처럼 "등록됐지만 도달 불가한 죽은 설정"이 된다. (2026-08-26)

## 4. 불변식

구체 항목·검증 명령은 `_shared/system-invariants.md`. 시스템 수정 후 그 자가점검을 돌린다.
