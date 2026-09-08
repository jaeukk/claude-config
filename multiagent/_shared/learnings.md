# Shared Learnings

작업 완료 후 재사용 가능한 교훈만 추가. append-only.  
중복·일회성·작업 특화 내용은 기록하지 말 것.

## 분류 규칙 (어디에 적을지)

- **시스템 운영 자체**에 대한, 어떤 작업에든 적용되는 교훈 → **이 파일** (`_shared/learnings.md`, git 추적·공개).
- **특정 외부 프로젝트/repo에 묶인** 교훈(예: mat·hwpx 내부) → **`_local/learnings.md`** (git 추적 안 함·미배포. 없으면 새로 생성. 오케스트레이터는 명시 요청 없이는 로드하지 않음).

## 형식

```
## [YYYY-MM-DD] [작업명]
**교훈**: 한 문장. 다음 작업에 그대로 적용 가능한 형태로.
**근거**: 왜 그런지, 어떤 작업에서 발견했는지.
**worker**: [관련 worker명]
```

---

<!-- 이 아래부터 교훈 추가 -->

## [2026-05-13] [mat-mvp]
**교훈**: orchestrator-cwd가 git이 아니면 Task tool sub-agent 호출에서 worktree 격리가 실패할 수 있다. 다른 git repo를 다룰 때는 그 repo로 `cd` 후 claude를 시작하거나, worktree를 요구하지 않는 일반 에이전트로 폴백.
**근거**: claude-test(비-git) cwd에서 `subagent_type: claude` 호출 시 "Cannot create agent worktree" 에러. `general-purpose`로 재시도하니 격리 없이 성공.
**worker**: claude-main 호출 경로

## [2026-05-14] [mat-mvp]
**교훈**: `task.md`는 ` ```yaml ` 블록을 2개 갖는 게 표준 패턴(메타 + Worker Plan)이다. 어떤 키든 첫 yaml fence만 보는 파서는 깨진다 — 문서 전체의 모든 yaml block을 스캔하도록 작성할 것.
**근거**: mat의 `readPlannedWorkers`가 첫 fence 닫는 ``` 에서 return하는 바람에 `planned_workers`(두 번째 블록)를 못 봤다. codex-critic이 MAJOR로 잡고 fix iter로 수정.
**worker**: codex-critic (지적), claude-main (수정)

## [2026-05-14] [mat-mvp]
**교훈**: 같은 worker의 재호출(fix iter)은 별도 폴더 만들지 말고 같은 worker 폴더 안에서 `brief-fix.md` / `result-fix.md` 명명으로 진행. 1차 산출물·승인 기록을 보존하면서 변경 이력이 시각적으로 드러난다.
**근거**: codex-critic 리뷰 후 claude-main에 MAJOR 2건 패치 재호출 시 적용. `workers_approved`는 그대로 두고 brief/result 한 쌍을 추가하는 것만으로 충분했고 깔끔했다.
**worker**: claude-main (fix iter)

## [2026-05-14] [yt-thumbnail-multiagent]
**교훈**: MultiAgent 작업은 worktree 진입 금지. orchestration 산출물(`tasks/<task>/`)은 gitignore라 worktree에 만들어도 본체로 옮기려면 수동 복사 사족이 생긴다. tracked 시스템 파일도 단순 append/수정에 worktree+commit+merge는 과한 오버헤드.
**근거**: 배경 세션 harness가 자동으로 EnterWorktree를 강제해 task 폴더와 시스템 파일 수정 양쪽에서 `cp -R` 또는 머지 사족이 발생했다. 외부 `target_repo` 쓰기는 codex-main의 cwd로 따로 격리되므로 MultiAgent repo 자체에 워크트리는 불필요. 인터랙티브 세션에서는 EnterWorktree를 자발적으로 호출하지 말 것.
**worker**: orchestrator (세션 초기화 시 EnterWorktree 호출 안 함)

## [2026-05-14] [yt-thumbnail-spring]
**교훈**: log.md는 표준 형식 엄수 — (a) 태그는 정해진 6종(`DECISION | WORKER_CALL | VERIFICATION | ERROR | APPROVAL | COMPLETE`)만 사용, (b) 타임스탬프 `[YYYY-MM-DD HH:MM]`까지 기록, (c) 작업 완료 시 마지막 줄에 `[COMPLETE]` 엔트리 필수.
**근거**: yt-thumbnail-spring log에서 `INIT/BRIEF/CALL/RESULT` 새 태그 사용, HH:MM 누락, [COMPLETE] 부재. mat 같은 도구가 표준 형식 가정하고 파싱하면 일관성 깨짐.
**worker**: orchestrator (로그 작성 규율)

## [2026-05-15] [hwpx-math-final]
**교훈**: codex MCP 호출이 비정상적으로 길어질 때(>2-3분) 첫 의심은 외부 MCP 도구 hang이지 모델·reasoning이 아니다. `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`의 event timestamp gap을 보면 어느 function_call에서 막혔는지 즉시 식별 가능.
**근거**: 표면 원인(reasoning=high, brief 길이, AGENTS preamble)으로 잘못 짚었다가 사용자 재질문 후 turn timing 분석으로 진단. 탐색·normalize는 50초, hang난 function_call→output 사이가 399초로 명확. session jsonl이 정답지.
**worker**: orchestrator (디버깅 절차)

## [2026-05-15] [hwpx-math-final]
**교훈**: `mcp__codex__codex`의 reject 응답이 codex backend 작업을 중단시키지 않는다. 사용자 거부 후에도 backend는 끝까지 실행되어 파일·부수 효과가 남을 수 있음. 거부한 호출 직후엔 대상 디렉토리 상태를 반드시 확인.
**근거**: reject된 codex MCP 호출 두 건이 backend에서 작업을 계속해 cwd에 산출 파일 생성. orchestrator는 처음에 그 파일들이 어디서 왔는지 추적 못 함. `~/.codex/sessions/` 세션 jsonl로 확인 가능.
**worker**: orchestrator (MCP reject 의미 이해)

## [2026-05-15] [manual-final-review]
**교훈**: `mcp__gemini-pro__*`(로컬 프록시 기반 gemini-pro 브리지)가 `Proxy 400 INVALID_ARGUMENT`를 내면 프롬프트 크기 문제가 아니라 모델 티어 문제일 수 있다 — 압축 재시도로 시간 쓰지 말고 폴백 순서를 `pro-high → pro-low(같은 프록시, 종종 정상) → Flash 브리지`로 단계 강등하라. 어느 경우든 model deviation을 result.md·리포트에 명시한다. gemini는 FS 접근이 없어 brief "경로 참조"가 안 통하므로 필요한 자료는 orchestrator가 MCP prompt에 직접 inline하고 그 사실을 brief·log에 적는다. FS 미접근 모델이 낸 *시스템 사실 주장*은 codex-critic/권위문서로 교차검증 후에만 채택한다(never-trust-upstream — 리뷰어 출력에도 동일 적용).
**근거**: pro-high가 큰/압축 프롬프트 모두 동일 400. Flash는 1회 성공했으나 문서 우선순위를 오추정, 같은 프롬프트로 pro-low는 정상 동작하며 더 날카로운 비평을 냈다(같은 프록시인데 pro-high만 막힘). pro-low조차 매뉴얼 용도(런타임 미적재 사람용 문서)를 오판해 "이론=토큰낭비"라는 틀린 전제로 소절 삭제를 권고 → 사실검증으로 불채택했다.
**worker**: gemini (프록시 장애·FS 미접근), codex-critic (사실 교차검증), orchestrator (폴백 강등·리뷰어 출력 검증)

## [2026-05-19] [repo-consistency-audit]
**교훈**: 다중 repo 일관성 감사에서 claude-main·codex-main을 **추상화 레이어로 분담**시키면(claude-main=의미·규칙 레벨, codex-main=파일·파서·코드 레벨) 같은 입력 중복 호출 대신 상호보완 커버리지가 나온다 — 이번에 codex만 검출(표준 brief→mat 파서가 worker 목적을 ` ```yaml `로 표시)·claude만 검출(manual↔mat 상태 우선순위 순서/단계 불일치)이 각각 진성 크리티컬이었고 둘 다 독립 검출한 항목(gemini 기본 모델 pro-high 충돌)은 신뢰도 최상으로 분류. 병렬 brief에 "다른 worker 결과 미참조" 명시는 codex result checklist에 그대로 확인됨. 또한 claude-main이 초기 가설 2건을 self-retract했어도 orchestrator가 인용 라인을 sources에 **직접 재대조**(never-trust-upstream을 worker 출력에도 적용)해야 false-positive·false-negative 둘 다 막힌다.
**근거**: 단일 worker였으면 크리티컬 3건 중 1건씩 누락. orchestrator 재검증에서 firstMeaningfulLine(task.go:499)·.mcp.json·routing.md:111을 직접 확인해 codex/claude 주장과 retraction을 모두 사실검증 후 취합.
**worker**: claude-main(의미·규칙 레이어), codex-main(파일·파서 레이어), orchestrator(레이어 분담 설계·인용 직접 재대조·취합)

## [2026-05-25] [autokakao-dup-guard]
**교훈**: 안전장치 코드의 codex-critic 비평을 반영할 때, Orchestrator가 비평을 **직접 재현 검증**하면(순수함수=단위테스트로, 구조적 결함=정적 grep/인덱스 비교로) 2차 worker 검수 호출 없이도 루프를 신뢰성 있게 종료할 수 있다 — 비평 맹신·맹기각 둘 다 회피. 이번엔 #3(정규화 충돌 `verify_room('스터디 2','스터디')=True`)을 단위로, #2(제목 후보 수집범위=메인창 전체→거짓양성)·#1(Enter가 포커스검증보다 먼저)을 정적으로 재현해 진성임을 확정하고, v2도 같은 방식으로 재검증(9케이스+정적 8항목 PASS) 후 사용자가 2차 검수 대신 수락. 더불어 안전장치는 **미확정 의존성(여기선 열린 방 헤더 AX 위치)을 파라미터+TODO로 외부화하고 미설정 기본값을 fail-closed**(전부 거부)로 두면, 라이브 검증 전 단계에서 절대 오발송이 안 나는 안전한 중간 산출물이 된다.
**근거**: codex High 3건이 모두 진성이었고 Orchestrator 재현으로 확정. read_open_room_title이 expected와 일치하는 후보를 메인창 어디서든 신뢰하던 v1은 "거짓 음성 방향" 주장과 달리 거짓 양성(오발송) 경로였음 — worker 자기평가도 never-trust-upstream로 교차검증해야 함. v2는 HEADER_* 미설정=항상 None=fail-closed로 안전하게 게이트.
**worker**: claude-main(구현·v2 반영), codex-critic(High3 비평), orchestrator(비평 직접 재현검증·fail-closed 수락 판단)

## [2026-05-25] [autokakao-jobs-demo]
**교훈**: 외부 GUI 자동화에서 "설계 단계의 가정"은 **라이브 테스트 전까지 미검증**으로 취급하라. 동명이인 안전장치를 브레인스토밍 때 전략 A(열린 방 헤더 제목 읽기)로 골랐지만, 라이브 probe 결과 KakaoTalk이 단일 창이라 헤더가 구분 가능한 AX 요소로 노출되지 않아 A는 원천 불가였다. 진짜 해법은 라이브 probe가 알려줬다 — ⌘F 검색 결과 셀(AXCell)의 `AXSelected`로 하이라이트를 읽어, room_title과 정확 일치하는 결과가 선택될 때까지 ↓ 후 Enter(전략 B). "첫 결과 ↓1회+Enter"는 '테스트' 검색이 '테스트1234'를 먼저 열어 오발송함을 라이브로 실증. 즉 GUI 자동화는 (1) 설계 가정에 과투자 말고 빨리 라이브 probe로 실제 AX 구조를 확인하고, (2) 안전장치는 '열고 나서 검증'(abort만 가능)보다 '정확한 대상을 애초에 선택'(B)이 더 강하다.
**근거**: 헤더 probe가 메인창 단일 창만 찾고(별도 창 없음) 열린 방 제목을 단일 요소로 못 줌. 반면 검색결과 probe에서 ↓1=테스트1234 selected, ↓2=테스트 selected가 깔끔히 노출돼 전략 B가 바로 구현됨. staging→--send 2/2 성공.
**worker**: orchestrator(라이브 probe·전략 전환·전략 B 구현), gemini(영수증·회의록 비전 정리)

## [2026-06-01] [harness-vup-reentry]
**교훈**: 외부 레퍼런스(harness)를 시스템에 도입하는 v-up에서, 6패턴을 통째로 받지 말고 **이 시스템 불변식으로 환원되는 것만 흡수하고 충돌하는 것은 "배제 근거를 design-basis(D6)에 명문화"**하는 방식이 정체성을 지킨다 — Pipeline/Fan-out·in/Expert Pool/Producer-Reviewer는 흡수(대부분 기존 암묵 구현, Fan-in 충돌해소만 신규), Supervisor·Hierarchical은 단일 orchestrator·worker간 무통신·file-as-memory와 충돌해 배제. codex-critic adversarial 리뷰가 진성 결함 2건(치명)을 잡음: ①재진입 분기를 result.md 유무로만 판단하면 status=waiting_<role>·늦은 응답·status↔log 불일치·외부 write_scope 재승인을 놓침 → 재정박에 brief+status 추가·분기 확장으로 해소, ②신설 불변식(INV11)의 grep이 `grep -lin`이라 "둘 중 하나만 맞아도 통과" → per-file `grep -q`+4패턴 positive+배제 negative check로 자동 FAIL 판정 가능하게 교정. 배제 근거 문구도 "Supervisor 개념 배제"가 아니라 "기존 orchestrator 위에 별도 long-lived 조정자/재귀 위임 **계층 추가**를 배제"로 정밀화해야 정확(orchestrator 자신이 이미 중앙 조정자이므로).
**근거**: orchestrator가 critic ISSUE 6건을 사실검증(never-trust-upstream을 리뷰어에도 적용) → #3만 PASS, 5건 진성 → 전부 반영. 자가점검 INV11a/b/c 신규 PASS, INV1~10 회귀 없음. 새 상시로드 비용은 CLAUDE.md 1줄 포인터뿐, 본문은 orchestrator-rules(온디맨드)·routing(라우팅시)·design-basis/invariants(게이트)에 배치.
**worker**: orchestrator(흡수/배제 설계·라이브 파일 편집·ISSUE 사실검증·자가점검), codex-critic(변경안 adversarial 리뷰 5 ISSUE)

## [2026-06-01] [model-policy-cleanup]
문서 일관성 변경(예: 모델 버전 문자열 → 별칭화)은 "정책 섹션"만 고치면 안 된다. 같은 식별자가 워커 상세·비용 설명·예시 등 여러 위치에 흩어져 있어, 한 곳만 바꾸면 같은 파일 안에서 정책↔본문이 모순된다. codex-critic이 routing.md의 잔존 핀(:62 claude-opus-4-7, :65 Opus 4.7, :120 gpt-5.4-mini)을 잡았다. → 표기 정책을 바꿀 땐 `grep`으로 그 식별자의 전 등장 위치를 먼저 훑고 일괄 처리할 것. 또한 "결정적/영속" 같은 단정어는 환경 설정(config·env·profile)으로 바뀔 수 있는 값엔 과장이므로 피한다.

## [2026-06-02] [gemini-backend-agy]
"pro-high 쓰지 마라"(D4/INV9) 같은 **환경 한계발 금지 규칙**은 그 환경(백엔드)이 바뀌면 근거가 사라진다. pro-high 제외 사유는 옛 antigravity-claude-proxy의 `400 INVALID_ARGUMENT`였는데, 백엔드를 `agy` CLI로 바꾸니 pro-high가 정상 작동(spike 실증). → 금지 규칙엔 **"무엇 때문에 금지인지(원인 계층)"를 함께 적어야**, 원인이 사라졌을 때 안전하게 해제할 수 있다. 또 모델 셀렉션이 도구마다 다름을 확인: agy는 모델이 **전역·계정단위**(`/model`)라 per-call 핀 불가 → worker별 다른 모델 동시 사용은 안 되고, gemini 전용 전역을 pro-high로 고정해 운용. 마이그레이션은 D4·INV9·INV10·routing·validate C6를 **한 묶음으로** 갱신해야 내부 모순(validate가 새 정본을 FAIL)이 안 생긴다.
**근거**: agy spike S1 GREEN + 3자 검수(codex #8이 "옛 정책과 충돌" 지적 → 검증하니 정책을 갱신해야 하는 것이었음). backends.json이 gemini 호출 정본, mcp__gemini-pro__/mcp__gemini__ 브리지 폐기.
**worker**: orchestrator(마이그레이션·라이브 편집), codex-critic+gemini=agy(검수)

## [2026-07-28] [orchestration-model-remap]
**교훈**: 이 시스템의 모델 배정은 **두 층**이고, 정책층(`policy/backends.yaml`)의 Claude `model:` 값은 **선언적**이다 — 엔진의 유일한 디스패치 경로 `dispatch_codex()`가 `--model`을 codex에만 넘기고(`policy_engine.py:392`), Claude 쪽은 PreToolUse 훅이 role→backend 해석·family만 검사할 뿐 모델 문자열을 읽지 않는다. 따라서 `backends.yaml`만 고치면 **실제로는 아무것도 안 바뀐다**. 세션 레버 3곳(`multiagent/.claude/settings.json`, `engine/adapters/install_wsl_orchestration.js`, `.claude/agents/*.md` frontmatter)을 함께 고쳐야 한다. 특히 installer는 `settings.model`을 하드코딩으로 되돌려 놓으므로, 고치지 않으면 다음 실행에서 조용히 원복된다. 같은 이유로 **effort도 비대칭**: bindings의 `effort`는 codex에만 도달하고 Claude 쪽 실효 레버는 agent frontmatter다 → 엔진(`VALID_EFFORTS`)을 건드리지 않고 frontmatter만으로 xhigh를 얻을 수 있었다(엔진 개정과 그에 딸린 검증 공백을 회피).
**근거**: 재배정 전 `policy_engine`을 in-memory로 로드해 목표 상태를 시뮬레이션 → `validate-policy errors: []`, 6개 role 해석, critic/verifier 양방향 독립성(author=codex → claude-core / claude-mid) 확인 후에야 파일 수정. 미바인딩 백엔드(`claude-ceiling`=Fable 5)도 validate 통과함을 같은 방법으로 사전 확인 — `validate_policy`는 "바인딩이 참조하는 백엔드가 존재하는가"만 보지 "모든 백엔드가 바인딩되었는가"는 안 본다. 2026-07-25 동일 작업이 예산 소진으로 통째 revert된 전례가 있어 `_local/`의 revert 기록을 먼저 읽고 재적용한 것이 비용을 크게 줄였다.
**worker**: orchestrator(정책 시뮬레이션·라이브 편집·배포·자가점검)

## [2026-08-25] [conductor-eligibility-generalized]
**교훈**: "X는 구조적으로 불가능"이라고 적힌 불변식은 **어느 계층의 한계인지** 함께 적지 않으면 수명을 넘겨
살아남는다. conductor를 claude-code로 고정한 근거는 "Codex child API는 Codex family만 스폰 가능"이었는데,
이는 **하나의 디스패치 수단**의 한계였을 뿐 호스트의 한계가 아니었다 — `claude`·`codex`·`agy`는 전부 CLI이므로
서브프로세스로 교차 벤더 호출이 된다(실증: codex/codex-conductor 계약으로 Claude worker dispatch exit 0).
2026-07-13 D4/INV9의 "pro-high 금지"와 **같은 실패형**이다(원인 계층 미기재 → 원인 소멸 후에도 규칙 잔존).
→ 금지·고정 규칙에는 반드시 "무엇이 이것을 참으로 만드는가"를 적고, 그 조건을 **엔진이 계산하게** 만들어라.
여기서는 상수 대신 3조건(후보 등재·어댑터 존재·`dispatch_hosts` 독립 도달성)으로 대체했다(D12).

**부수 교훈 — 검토자에게 자기 권고를 다시 물어라**: codex-critic 2라운드는 "agy 디스패처를 추가하라"고
권고했고 그대로 구현했으나, 3라운드에서 같은 검토자가 "격리가 거짓이니 이번 릴리스에선 빼라"고 **자기 권고를
뒤집었다** — 2라운드 권고가 "제대로 격리된 디스패처"를 전제했기 때문이다. 모순이 아니라 정보 증가다.
라운드를 반복하면 이런 전제 붕괴가 드러난다. 1회 검토였다면 `--dangerously-skip-permissions`가 "격리됨"
라벨을 달고 그대로 나갔을 것이다.

**부수 교훈 — 플래그를 기억으로 쓰지 마라**: 1라운드에서 `--allowedTools`(허용 추가일 뿐 배타적 제한이 아님)와
"claude CLI에는 effort 플래그가 없다"(있다: `--effort`)를 둘 다 틀렸고, 검토자가 `--tools`를 지목해 바로잡았다.
`--help`를 읽는 데 드는 비용이 잘못된 격리 주장을 문서에 박제하는 비용보다 훨씬 싸다.

**근거**: codex-critic(Sol, high) 3라운드 감사 — 9건 → 7건 → 6건, 전부 수용. Claude worker의 도구면은
실측 확인(도구 나열 요청에 "Glob, Grep, Read"만 응답 = Bash·MCP 제거됨). agy 경로는 쿼터 소진으로 미검증.
**worker**: orchestrator(설계·구현·검증), codex-critic=codex-high(3라운드 독립 감사)

## [2026-09-08] [orca-adapter-authorship]
**교훈 1 — 같은 결함이 라운드마다 다시 나오면, 인터리빙을 또 막지 말고 술어를 다시 유도하라.**
4·5·6라운드는 한 버그를 세 번 잡은 것이다: 잠금 밖 스냅샷(TOCTOU) → `starting`만 막고
`outcome_unknown`은 안 막음 → 결국 "baseline과 **다르면** 이 attempt의 것"이라는 술어 자체가
틀렸음(baseline보다 **오래된** dispatch도 다르다). 앞의 두 수정은 기계적(잠금 안으로 옮기기,
상태 하나 추가)이라 각각 사례 하나만 닫았고, 마지막 수정만 **의미적**이었다 — "Orca가 지금
current라고 보고하는 dispatch(같은 잠금 아래에서 읽음)이면서 baseline과 다른 것"만 이 attempt의
것. 그제서야 클래스가 닫혔다. 검토자는 7라운드에서 Orca 번들 소스의 SQL(`ORDER BY rowid DESC
LIMIT 1`)까지 열어 "current"의 의미를 확정하고 SHIP을 냈다. → 리뷰 루프가 수렴하지 않으면
타이밍이 아니라 **불변식의 정의**를 의심하라.

**교훈 2 — 수정을 쓴 쪽이 저자다. 자기 발견을 자기가 고치면 미검토 산출물이 된다.**
Codex가 2라운드 감사(11건)를 내고 스스로 전부 구현했다(150→627줄). 그 결과물은 아무도 보지
않은 상태였고, 독립성 규칙대로 Claude ceiling(fable-5-1 high)에 보내자 9건이 나왔다 — 그중
"성공 경로가 한 번도 실행된 적 없다"(receipt 키 이름·dispatch-show 형태·terminal 상태가 전부
추측)와 "거부된 launch가 run을 영구 잠금"(P1)이 포함. 저자 family는 **마지막으로 파일을 쓴
손**이며(last producer wins), 그게 검토자를 결정한다. 이번 세션에서 이 규칙을 엔진(sidecar)과
Orca 어댑터(run별 record) 양쪽에 코드로 박았다.

**교훈 3 — 추측 하나당 실측 하나. 한 번의 라이브 프로브가 한 라운드의 논쟁보다 싸다.**
실측으로 확정한 것: worker-start receipt는 `result.dispatchId`/`requestId`(최상위); 거부
envelope는 `ok:false` + `error:{code,message,data}`; dispatch 없는 task의 dispatch-show는
`ok:true, dispatch:null`; receipt `timeoutMs=60000`. 각각 검토자의 가정 하나를 대체했고, 그
전까지는 "동작할 것"이었지 "동작한다"가 아니었다. 모델 활동도 마찬가지 — Codex의 자기보고("아마
gpt-6-astra")가 아니라 Orca receipt의 `requested==effective`가 근거다.

**교훈 4 — 전송층을 바꾸면 저자 증거는 따라오지 않는다.**
엔진 계약(task.yaml 옆 sidecar)과 Orca run(`tasks/orca/<run_id>/`)은 **별도 저장소**다.
Codex가 Orca에서 만든 산출물을 엔진 경로로 검토하려니 conductor-family fallback이 "Claude가
썼다"고 판정해 거부했다 — 정확한 동작이지만 진실은 아니었고, sidecar를 손으로 적어 다리를 놓아야
했다(출처를 source 문자열에 남김). SKILL.md에 "separate store, 자동 이월 없음"으로 명문화.

**비용**: codex-ceiling(astra medium) 7라운드 ≈ 43만 토큰 + Claude ceiling 1라운드 + 라이브 워커
5개(runner·verifier). 삭제 리스트 적용으로 664→578줄; 최종 SHIP.
**근거**: `multiagent/tasks/orca-adapter-audit/round{1..6}-findings.md`, `result7.md`(git 미추적);
커밋 `406a662`.
**worker**: orchestrator(구현·검증·라이브 프로브), codex-ceiling=gpt-6-astra medium(7라운드 감사),
claude-ceiling=fable-5-1 high(Codex 산출물 1라운드 감사), Orca 워커 runner/verifier(라이브 검증)


> Historical import, preserved verbatim from /home/jaeukk/_shared/learnings.md; snapshot: A.tgz.

## [2026-07-22] [priorart-stipple-002-codex-cli-fallback]
**교훈**: 세션에 `mcp__codex__*` MCP 도구가 아예 안 잡혀 있어도 codex-critic/verifier를 포기하지 말 것 — `codex` CLI 바이너리(`~/.local/bin/codex`)가 있으면 `codex exec -s read-only -C <target_repo> -m <model> -` (stdin으로 brief 전달)로 직접 호출 가능하며, backends.json의 fallback 항목이 이미 이 경로를 정의해 두었다. 다만 `~/.codex/config.toml`의 기본 모델(`gpt-5.1-codex-max`)은 ChatGPT-계정 인증에서 `400 invalid_request_error`로 거부될 수 있고, 그럴 때 다른 모델명을 추측해서 계속 호출 시도하는 것(실제로 5개 추측 모두 실패)은 실제 과금 API에 낭비 호출을 쌓는 짓이다 → 1-2회 추측 실패 시 즉시 멈추고 사용자에게 정확한 모델 문자열을 물을 것. 이번엔 사용자가 `gpt-5.6-sol`/`gpt-5.6-terra`를 제공, 스모크테스트 후 정상 작동 확인.
**근거**: `codex doctor`가 `auth mode: chatgpt`를 보여줬고, config.toml 기본값과 5개 추측 모델 전부 동일한 400 에러. 사용자 제공 모델 2종은 즉시 성공(토큰 사용량 몇 천 수준의 저비용 smoke test로 먼저 확인 후 실제 브리핑 투입).
**worker**: orchestrator(환경 진단·CLI 폴백 발견·과호출 방지 판단)


> Current-policy note: operational guidance above is superseded. Dispatch engine-managed workers through `policy_engine.py dispatch-worker`; this historical direct-CLI example is not current dispatch permission. D14 disables agy/Gemini workers. The historical full-match validation claim is not a claim about the current engine: its documented agy model check uses the `gemini-` prefix. See `_shared/design-basis.md` D14, `docs/architecture.md`, and `engine/adapters/claude_pretool.py`.


> Historical import, preserved verbatim from /home/jaeukk/_shared/learnings.md; snapshot: A.tgz.

## [2026-07-31] [agy-integration]
**교훈**: 백엔드 레지스트리에서 **host는 family를 결정하지 않는다**. agy 한 바이너리가 Gemini·Claude·gpt-oss 3개 벤더를 서빙하므로, `host: agy`를 보고 `family: gemini`를 추론하면 `model: claude-sonnet-4-6`을 등재해도 아무 에러 없이 통과하고 critic/verifier의 `different_family_from_author`가 **조용히** 무력화된다 — 시스템이 "독립 검증 완료"라고 기록하는 바로 그 지점에서 속성이 소실되는 유형이라 다운스트림 어디에서도 감지되지 않는다. 방어는 반드시 `validate_policy` 층이어야 하고(문서 규약은 검사되지 않는다), 접두사 검사만으로는 부족하다: `gemini-claude-sonnet-4-6`이 `startswith("gemini-")`를 통과한다 → family 일치 **AND** `gemini-\d…` 완전일치로 이중 검사할 것. 두 번째 교훈: **"3rd family = 벤더 장애 failover"는 과장이었다.** `resolve_binding`은 가용성을 탐지하지 않고 첫 적격 후보를 반환하므로 3순위 후보는 정상 경로에서 결코 선택되지 않는다 — 얻는 것은 "장애 시 갈 곳이 존재한다"는 구조적 여지이지 자동 전환이 아니다. 아키텍처 근거를 쓸 때 이 둘을 구분하지 않으면, 정작 그 장애가 났을 때 처음으로 들통난다.
**근거**: codex-critic이 음성 테스트로 3가지 우회(family 위조·family 오기·접두사 위장)를 모두 PASS시켜 실증했고, resolver 실측으로 `agy-multimodal`이 어떤 정상 author 조합에서도 선택되지 않음을 보였다. 수정 후 4종 음성 테스트 전수 REJECTED·기준선 validate-policy/self-test 유지 확인. 교차 벤더 리뷰가 자기 설계의 과장을 잡아낸 사례 — 같은 family였으면 놓쳤을 가능성이 높다(이 페이지 자체가 그 논거다).
**부수 교훈(운영)**: 외부 CLI를 `subprocess`로 부르는 코드의 **실패 경로**를 단위 테스트할 땐 반드시 `PATH`를 격리하고 돌릴 것. "실행파일 부재" 분기를 시험하려다 PATH를 그대로 둔 채 호출해 미승인 워커(agy)를 3회 실기동시키고 쿼터를 소모했다(승인 게이트 위반, log.md 09:23 자진 신고).
**worker**: orchestrator(설계·구현·정정), codex-critic(독립 검증 — Blocking 7건 중 6건 인정)

> Current-policy note: operational guidance above is superseded. Dispatch engine-managed workers through `policy_engine.py dispatch-worker`; this historical direct-CLI example is not current dispatch permission. D14 disables agy/Gemini workers. The historical full-match validation claim is not a claim about the current engine: its documented agy model check uses the `gemini-` prefix. See `_shared/design-basis.md` D14, `docs/architecture.md`, and `engine/adapters/claude_pretool.py`.
