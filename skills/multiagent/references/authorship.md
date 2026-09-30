# Authorship

Moved verbatim from SKILL.md (2026-09-30). `policy_engine.py review` covers the common case (reviewing work this session's family produced); this page is for everything else.

A reviewer is cleared against what was **observed** producing the artifact, recorded in
`observed-author.json` beside the contract. `dispatch-worker` records a CLI producer
(`implementer`, `bulk_worker`) after exit 0, or under `--write` whenever the change set is
non-empty, failure included. The hook records a
native producer before the call runs, since PreToolUse cannot see the outcome; a session that did
not load the hook records nothing. The record accumulates every family that produced part of the
artifact.

`critic` and `verifier` are refused when the record is unreadable or names an unknown family; when
it names more than one family (mixed: split the artifact or review by hand); when it contradicts
`author_family` (fix the contract, not the sidecar); when a `--write` reservation recorded no
outcome; and when `roles_plan` includes a producer but nothing was observed. The conductor's family
stands in only for a contract that planned no producer.

`record-author --task-dir … --family … --source …` stores an **assertion**. It only adds families a
reviewer must differ from. It stands in for a missing observation only when the user has
recorded `authorship_assertion` under `approvals.user`; then assert every producing family. The
engine trusts `approvals.user` as the user's word; it authenticates nobody.

A `--out` publication also records its producer in `outputs/<dispatch_id>.json`; a `runner`
publication is attributed only there.
