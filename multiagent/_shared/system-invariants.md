# System Invariants — 시스템 수정 후 자가 점검

> **로드 정책**: 평소 미로드. 시스템 파일 수정·검증 작업일 때만 (`orchestrator-rules.md` §2).
> 목적: 시스템 변경 후 **전면 멀티에이전트 재감사 대신** 이 점검만 돌려 모순 재발을 잡는다.
> 통과해야 커밋. 깨지면 고치거나, 의도된 변경이면 `design-basis.md` 결정(D*)·이 표를 함께 갱신.

## 불변식 목록

| ID | 불변식 | 깨지면 |
|---|---|---|
| INV1 | `write_scope` 값 집합이 CLAUDE.md(정의처)·routing.md·_templates/worker-brief.md·task-folder.md·매뉴얼에서 동일 (`none`/`tasks-only`/패턴) | D1 위반 — 어디든 한 곳만 다르면 시스템 자체 모순 |
| INV2 | codex-critic 선행조건에 "claude-main result.md 존재 필수" 같은 **전용 강제** 표현 없음 (일반화 표현이어야) | D2 위반 |
| INV3 | log 태그 = 정확히 `DECISION\|WORKER_CALL\|VERIFICATION\|ERROR\|APPROVAL\|COMPLETE` 6종 (_templates/log.md, 매뉴얼) | 파서·일관성 깨짐 |
| INV4 | context.md 한도 1500자, brief 한도 1200자 수치가 CLAUDE.md·매뉴얼·_templates 헤더에서 동일 | 한도 불일치 |
| INV5 | **(유지보수자 전용)** 외부 매뉴얼 메인 섹션 개수 == manual-repo `CLAUDE.md`의 메인 섹션 목록 개수 | 매뉴얼↔manual-repo 빌드 스펙 불일치 (현재 R3 미해소 시 의도적 FAIL) |
| INV6 | 매뉴얼 `workers_approved` 예시 스키마가 approval-policy.md와 일치 (`worker:`/date-only/`purpose:`/`approved_by:`, `HH:MM` 없음) | B1/B6 재발 |
| INV7 | 권위 우선순위 문구가 매뉴얼 §3과 design-basis.md §2에서 동일 (CLAUDE.md > routing/approval/orchestrator-rules > 매뉴얼) | Clash 해소 규칙 붕괴 |
| INV8 | D5 and orchestrator-rules require interactive execution, permit Orca-managed worktree terminals, forbid background/headless conductors, and preserve one conductor and one explicit persistent task directory | D5 violation |
| INV9 | **폐기 (D14, 2026-08-26)** — gemini/agy 백엔드는 비활성이다. `_shared/backends.json`에 `gemini`·`gemini-reader` 워커가 **없어야** 한다 | 비활성화한 백엔드가 되살아나 승인 없이 호출됨 |
| INV10 | 폐기 브리지 **`mcp__gemini__gemini_*`(CLI 래퍼) 및 `mcp__gemini-pro__*`(프록시)** 가 routing.md·task-folder.md·CLAUDE.md에 **활성 호출**로 없음. 잔여 언급은 **폐기 안내 문맥에서만** | C2 재발 — 폐기 브리지 잔존 호출이 즉시 실패 (D4 위반) |
| INV11 | 재진입 프로토콜이 orchestrator-rules.md §3 **와** CLAUDE.md Task Lifecycle 포인터에 **둘 다** 존재. routing.md 토폴로지표에 4패턴(Pipeline/Fan-out·in/Expert Pool/Producer-Reviewer) 모두 존재하고, Supervisor·Hierarchical은 "배제" 줄에만 등장(채택표 행으로 등장 금지) | D6 위반 — 재진입/패턴 규정 유실 또는 배제 패턴 부활 |
| INV12 | 카파시 4원칙: CLAUDE.md에 "운영 원칙 (Operating Principles)" 섹션 존재, _templates/worker-brief.md에 "Worker 행동 규약" 고정 블록 존재, **블록 안에 사용자질문 지시(질문/ask) 없음**, worker-result.md 체크리스트에 표면화 항목 존재 | D8 위반 — 층별 적용 붕괴(워커 one-shot 구조와 모순) 또는 워커 규약 유실 |
| INV13 | **(System B 설치본 한정)** agy 백엔드 정합성 — `policy/backends.yaml`의 `host: "agy"` 항목은 **전부** `family: "gemini"`이고 `model`이 `gemini-` 접두. `engine/policy_engine.py`의 bulk family 검사가 **부분집합**(`<=`)이며 정확일치(`!=`)로 되돌아가 있지 않음. `validate-policy`·`self-test` 통과 | D11 위반 — agy 경유 타벤더 모델이 family를 오표기해 critic/verifier의 different-family 독립성이 **조용히** 붕괴하거나, 3번째 family가 validate에서 거부됨 |

> ※ **매뉴얼(외부 repo) 비교 항목은 유지보수자 전용(optional)**. 공개 설치본에는 매뉴얼이 없으므로 핵심 점검(INV1–4·6–12)은 시스템 파일 자체 일관성만 본다. INV5와 각 INV의 매뉴얼 측 일치 검사, INV12e/f의 3 flavor 교차 점검은 아래 스크립트의 optional 블록에서 해당 자산이 있을 때만 실행된다.

## 자가 점검 스크립트

`<설치한-폴더>`에서 실행. 핵심 블록은 시스템 파일만 검사하므로
**공개 설치본도 매뉴얼 없이 그대로 실행**된다. 유지보수자 블록은 외부 매뉴얼이 있을 때만 돈다.

```bash
# ── 핵심 자가점검 (시스템 파일만; 외부 매뉴얼 불필요) ──
ROOT=<설치한-폴더>

echo "INV1 tasks-only 분포 (CLAUDE/routing/templates 모두 존재해야)"
grep -l 'tasks-only' "$ROOT/CLAUDE.md" "$ROOT/_shared/routing.md" \
  "$ROOT/_templates/worker-brief.md" "$ROOT/_templates/task-folder.md"

echo "INV2 codex-critic 전용 강제 표현 (출력 없어야 PASS)"
grep -n 'result.md. 존재 필수\|claude-main 결과 필요 → 항상 후행' "$ROOT/_shared/routing.md"

echo "INV3 log 태그 (_templates/log.md 에 6종 정의 라인 확인)"
grep -n 'DECISION | WORKER_CALL | VERIFICATION | ERROR | APPROVAL | COMPLETE' "$ROOT/_templates/log.md"

echo "INV4 한도 수치 (1500 / 1200 각 파일)"
grep -rn '1500자\|1200자' "$ROOT/CLAUDE.md" "$ROOT/_templates/context.md" "$ROOT/_templates/worker-brief.md"

echo "INV6 workers_approved HH:MM 잔존 (approval-policy.md; 출력 없어야 PASS)"
grep -n 'approved_at: <YYYY-MM-DD HH:MM>' "$ROOT/_shared/approval-policy.md"

echo "INV7 권위 우선순위 문구 (design-basis 에 존재해야)"
grep -liE '권위 우선순위|CLAUDE.md가 가장 높|문서가 충돌' "$ROOT/_shared/design-basis.md"

echo "INV8 interactive conductor, permitted Orca worktrees, persistent task directory"
python3 -B - "$ROOT" <<'PY_INV8'
from pathlib import Path
import sys
root = Path(sys.argv[1])
required = ['Interactive sessions only.', 'Orca-managed terminals in git worktrees are permitted.', 'Background or headless conductor sessions are forbidden.', 'Exactly one conductor terminal owns each task.', 'All participants use the same explicit task directory for persistent task records.']
for name in ("design-basis.md", "orchestrator-rules.md"):
    text = (root / "_shared" / name).read_text()
    for sentence in required:
        assert sentence in text, (name, sentence)
print("INV8 PASS")
PY_INV8
[ "$?" -eq 0 ] || { echo "INV8 FAIL"; exit 1; }
test -f "$ROOT/AGENTS.md" && test ! -L "$ROOT/AGENTS.md" && cmp "$ROOT/CLAUDE.md" "$ROOT/AGENTS.md" || { echo "AGENTS drift FAIL"; exit 1; }
echo "AGENTS drift PASS"

echo "INV9 gemini 비활성 (backends.json에 agy/gemini 워커가 없어야; 출력 없어야 PASS)"
grep -n '"command": "agy"' "$ROOT/_shared/backends.json"
grep -n '"gemini' "$ROOT/_shared/backends.json"

echo "INV10 폐기 브리지 호출형 mcp__gemini__gemini_* / mcp__gemini-pro__ 활성호출 (출력 없어야 PASS)"
grep -rn 'mcp__gemini__gemini_' "$ROOT/_shared/routing.md" "$ROOT/_templates/task-folder.md" "$ROOT/CLAUDE.md"
echo "INV10b mcp__gemini__* / mcp__gemini-pro__ 잔여 언급 — 전부 '폐기' 안내 문맥이어야 (호출·예시·「또는」 선택지면 FAIL)"
grep -rn 'mcp__gemini__\|mcp__gemini-pro__' "$ROOT/_shared/routing.md" "$ROOT/_templates/task-folder.md" "$ROOT/CLAUDE.md"

echo "INV11a 재진입: orchestrator-rules §3 + CLAUDE.md 포인터 둘 다 (둘 다 PASS 떠야)"
grep -q '재진입 프로토콜' "$ROOT/_shared/orchestrator-rules.md" && echo " orchestrator-rules PASS" || echo " orchestrator-rules FAIL"
grep -q '재진입 프로토콜' "$ROOT/CLAUDE.md" && echo " CLAUDE.md PASS" || echo " CLAUDE.md FAIL"
echo "INV11b 토폴로지 4패턴 모두 존재 (4개 PASS 떠야)"
for p in 'Pipeline' 'Fan-out/Fan-in' 'Expert Pool' 'Producer-Reviewer'; do
  grep -q "$p" "$ROOT/_shared/routing.md" && echo " $p PASS" || echo " $p FAIL"
done
echo "INV11c Supervisor/Hierarchical 은 '배제' 줄에만 (배제 아닌 등장 0이어야 PASS)"
grep -nE 'Supervisor|Hierarchical' "$ROOT/_shared/routing.md" | grep -v '배제' || echo " (배제 외 등장 없음 = PASS)"

echo "INV12a 운영 원칙 섹션 (CLAUDE.md 에 존재해야)"
grep -n '운영 원칙 (Operating Principles)' "$ROOT/CLAUDE.md"
echo "INV12b Worker 행동 규약 고정 블록 (worker-brief 에 존재해야)"
grep -n 'Worker 행동 규약' "$ROOT/_templates/worker-brief.md"
echo "INV12c 블록 내 사용자질문 표현 (출력 없어야 PASS)"
sed -n '/^## Worker 행동 규약/,/^## Execution/p' "$ROOT/_templates/worker-brief.md" | grep -inE '질문|ask' || echo " (없음 = PASS)"
echo "INV12d result 체크리스트 표면화 항목 (존재해야)"
grep -n '표면화' "$ROOT/_templates/worker-result.md"

# ── 유지보수자 전용 (optional): 3 flavor 교차 점검 (generator templates 있을 때만) ──
TPL="$ROOT/plugins/multi-agent-starter/skills/configure-multiagent/generator/templates"
if [ -d "$TPL" ]; then
  echo "[유지보수자] INV12e Operating Principles 섹션 — codex/antigravity AGENTS.md (2개 나와야 PASS)"
  grep -l 'Operating Principles' "$TPL/codex/AGENTS.md" "$TPL/antigravity/AGENTS.md"
  echo "[유지보수자] INV12f Worker 행동 규약 블록 — 3 flavor worker-brief (3개 나와야 PASS)"
  grep -l 'Worker 행동 규약' "$TPL/claude/_templates/worker-brief.md" \
    "$TPL/codex/_templates/worker-brief.md" "$TPL/antigravity/_templates/worker-brief.md"
else
  echo "(generator templates 없음 — 교차 flavor 점검 건너뜀. 설치본은 정상.)"
fi

echo "INV13 agy 백엔드 정합성 (System B 설치본에서만 실행)"
if [ -f "$ROOT/policy/backends.yaml" ]; then
  python3 - "$ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
bad = [
    a for a, b in json.loads((root / "policy" / "backends.yaml").read_text())["backends"].items()
    if b.get("host") == "agy"
    and (b.get("family") != "gemini" or not str(b.get("model", "")).startswith("gemini-"))
]
print(" INV13a FAIL: " + ", ".join(bad) if bad else " INV13a PASS (agy 백엔드 전부 gemini family·gemini- 모델)")
sys.exit(1 if bad else 0)
PY
  INV13_RC=$?
  grep -q '{"claude", "codex"} <= bulk_families' "$ROOT/engine/policy_engine.py" \
    && echo " INV13b PASS (bulk family 검사 = 부분집합)" \
    || { echo " INV13b FAIL (정확일치로 회귀했거나 검사 유실)"; INV13_RC=1; }
  python3 "$ROOT/engine/policy_engine.py" --root "$ROOT" validate-policy >/dev/null 2>&1 \
    && echo " INV13c PASS (validate-policy)" || { echo " INV13c FAIL (validate-policy)"; INV13_RC=1; }
  python3 "$ROOT/engine/policy_engine.py" --root "$ROOT" self-test >/dev/null 2>&1 \
    && echo " INV13d PASS (self-test)" || { echo " INV13d FAIL (self-test)"; INV13_RC=1; }
  [ "$INV13_RC" -eq 0 ] || echo " ⚠ INV13 위반 — 커밋 금지"
else
  echo " (System B 미설치 — INV13 건너뜀. System A 전용 미러는 정상.)"
fi

# ── 유지보수자 전용 (optional): 외부 매뉴얼 일관성 ──
# 공개 배포본에는 매뉴얼이 없다. 매뉴얼 repo를 함께 관리하는 유지보수자 환경에서만 실행된다.
MANUAL=<매뉴얼-경로>/multi-agent-manual.txt
MANUAL_CLAUDE=<매뉴얼-경로>/CLAUDE.md
if [ -f "$MANUAL" ]; then
  echo "[유지보수자] INV5 매뉴얼 섹션 수 vs manual-repo CLAUDE.md (두 숫자 같아야; design-basis 현재값=10)"
  grep -nE '^[0-9]{1,2}\. ' "$MANUAL" | grep -viE 'brief에|task.md의|log.md에'
  grep -cE '^[0-9]{1,2}\. ' "$MANUAL_CLAUDE"
  echo "[유지보수자] INV1/4/6/7/8 매뉴얼 측 일치"
  grep -l 'tasks-only' "$MANUAL"
  grep -rn '1500자\|1200자' "$MANUAL"
  grep -n 'approved_at: <YYYY-MM-DD HH:MM>' "$MANUAL"   # 출력 없어야 PASS
  grep -liE '권위 우선순위|CLAUDE.md가 가장 높|문서가 충돌' "$MANUAL"
  grep -lin 'worktree\|배경\|백그라운드\|background session' "$MANUAL"
else
  echo "(외부 매뉴얼 없음 — 유지보수자 전용 점검 건너뜀. 공개 설치본은 정상.)"
fi
```

## 전면 재감사가 필요한 경우 (이 점검으로 부족)

- 새 외부 개념·레퍼런스를 시스템에 도입할 때 (개념↔규칙 매핑 자체가 바뀜)
- worker pool 구성·역할이 바뀔 때
- 위 불변식으로 표현 불가한 구조 변경
→ 그때만 `tasks/<new>/`로 새 점검 작업 + 필요 시 codex-critic/gemini. 그 외 일반 수정은 이 스크립트로 충분.
