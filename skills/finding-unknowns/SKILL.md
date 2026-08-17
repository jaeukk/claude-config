---
name: finding-unknowns
description: Surface material unknowns before costly or hard-to-reverse work — the gap between what was asked for and what the work actually requires. Use when invoked as /finding-unknowns, when the user asks for a blindspot pass or says "what am I missing" about a technical task, or when an unresolved choice would materially change the physics, data model, interface, success criteria, or an expensive/irreversible action (a long HPC job, a migration, a design commitment). Apply only the cheapest technique that closes the gap. "New feature" or "refactor" alone is not sufficient reason. Do NOT use for well-specified, routine, or mechanical work, or for non-technical "interview me" requests.
---

# Finding Your Unknowns

Adapted from Thariq Shihipar's *A Field Guide to Fable: Finding Your Unknowns* (Anthropic, 2026-07-03).
Excerpt note: `20_Notes/40_Resources/40_Talks/A Field Guide to Fable - Finding Your Unknowns.md`.

The prompt is the map; the codebase, the physics, and the data are the territory. Everything
unsaid gets filled in by a confident guess that propagates silently through the whole change.

**Purpose: expose material assumptions early enough that changing course is still cheap.**

## Step 0 — is there actually an unknown?

Default to skipping. Check the request for an immediately visible *material* ambiguity — one
whose resolution changes the architecture, the physics, the data model, or an irreversible
action. If none is visible, **inspect the minimum relevant territory first** (the module, the
neighboring experiment, the method section), then reassess once. Do not invent unknowns before
reading the available evidence — speculating from the prompt alone is the exact failure this
skill exists to prevent.

When a material unknown *is* present, start with the cheapest technique that can resolve it.
**Don't run the ladder mechanically — but don't stop at one either.** If what you learn changes
what must be decided, built, or reviewed, carry it into the next relevant step: a blindspot
changes what you ask, the answer changes the plan, the plan changes what counts as a deviation.
That chaining is the method; isolated tactics are not. A user-requested full pass may use the
whole cycle. On borderline tasks, state the assumption in one line and proceed. Don't announce
the classification itself.

## Calibrate to the person

Treat the user as a thought partner, not a specification source. Their **expertise level**
decides which blindspots are genuinely unknown to *them* and how much to explain; their
**starting point** decides whether an incomplete statement is tentative thinking or a settled
constraint. If either materially changes what you'd say or show and you can't infer it, ask
once — otherwise state the level you assumed.

Do not freeze tentative thinking into a rigid specification, and do not fill a genuine gap with
an unmarked guess. Those are the same failure seen from opposite ends.

## Discovery techniques

| Category | What it means here | Technique |
|---|---|---|
| **Known known** | Stated outright or evidenced in the code | Preserve as a constraint — no discovery step |
| **Known unknown** | The user knows there's a consequential gap | **Interview** |
| **Unknown known** | The user has a tacit criterion or convention | **Inspect examples**; prototype when reaction is needed |
| **Unknown unknown** | Something relevant the user hasn't considered | **Blindspot pass** |
| — | The criterion resists description in words | **References** |

### Blindspot pass

Read enough of the actual territory to have something to say, then report the **three
highest-consequence** blindspots first. For each: the evidence, the concrete consequence if
ignored, and the cheapest way to verify it. On an explicitly requested full pass, offer the
remaining independent high-consequence items in ranked order — three is the first batch, not a
ceiling.

Aim at what's specific to this task — constraints the code enforces that the request
contradicts, domain failure modes (units, normalization, convergence, boundary conditions,
quota, licensing), standard practice neither of you mentioned. Cite a file, function, equation,
paper, or authoritative doc when one exists; label an uncertain domain claim as a hypothesis
rather than a fact. Generic risk lists are worthless — cut them.

### Interview

Use when the request has more than one defensible reading and the readings lead to materially
different work.

- **Triggered automatically: ask at most one blocking question**, and only if the answer
  materially changes the physics, architecture, data model, or an irreversible action. Use a
  stated default when the choice is reversible or doesn't change the outcome.
- **Asked for explicitly:** one question at a time, wait for each answer, ordered by
  consequence — architecture before cosmetics. After four, summarize what's settled and
  continue only if material ambiguity remains.
- Never ask what you can determine yourself by reading the code.

### Inspect examples, then prototype

**Inspect first when the task calls for continuity** with an established convention — house
style, a neighboring experiment, an existing plot. That recovers tacit conventions without
manufacturing alternatives the project already rejected.

**Prototype when the user is choosing a new direction**, or when the examples don't settle a
recognize-it-when-I-see-it criterion. Precedent must not be allowed to suppress the question of
whether the user still wants that precedent.

Produce 2–4 *genuinely different* directions, not one option with three trims, in the cheapest
medium that supports the comparison: a Markdown comparison by default; HTML/Artifact or plotted
figures only when seeing them side by side is the whole point. People cannot specify taste, but
they reject on sight.

In a research repo, prototype code goes in the tracked `experiments/` layout and generated
figures go to the project's data area — the global rules apply to exploratory code too.

### References

When the user can't describe it, ask them to show it: a component, a paper figure, a repo, a
function whose shape they like.

Prefer **source** when behavior or semantics matter — semantics carry across languages. Use
screenshots or rendered figures when *appearance* is the requirement. Inspect both when you
can, and say which properties you're carrying over and which you're leaving behind.

## Phases — not alternatives

The table above selects *one discovery technique*. What follows are **phases**, governed by how
big and how reversible the task is. Skip them on small work; on substantial work they run in
sequence, each fed by the last.

### Plan — decision-first

Lead with the decisions most likely to change: data models, interfaces, units and conventions,
algorithm choice, UX flow. For each, give the choice, the alternative you rejected, and what
breaks if it's wrong.

Then include only the implementation steps needed to make verification clear, ending with
measurable success criteria. Decision-first ordering does not license dropping the checks.

### During — deviation record

Record **material** deviations when you hit them, not from memory afterwards: what the plan
said, what you did, and the edge case that forced it. Write them into an existing task or
orchestration log if the project defines one; otherwise keep a running `Deviations` section in
the active plan. For multi-attempt work that has to survive a handoff or a context reset, ask
before creating a temporary note — the point is that the *next* attempt inherits the learning,
which a summary written at the end cannot provide.

### After

Summarize the material assumptions and deviations in the final response. That's usually enough.

- **Shareable explainer** — fold the prototype, plan, and deviations into one document only
  when asked, or when an external reviewer (co-author, PI, PR reviewer) actually needs it.
- **Comprehension check** — when the user must personally approve, operate, merge, or defend a
  complex high-consequence change, ask 1–3 short questions about its rationale, assumptions,
  and failure modes, and give the answers after they respond. Never a merge gate unless they
  ask for one. The point is catching a quiet divergence between their mental model and the
  implementation — so don't state the answers up front, or it tests nothing.
- *Local adaptation, not from the source:* **preflight before anything expensive or
  irreversible** (a long Nurion job, a migration, a submission) — a short operational checklist
  of what the change does, with expected values stated. This checks completeness, not
  understanding; it complements the comprehension check rather than replacing it.

## Proportionality

Investigate only enough to resolve material uncertainty, scaled to cost and reversibility —
then pick the minimum sufficient implementation (this machine's always-on `ponytail` ladder).
Understanding and laziness are not in tension; the reading is what makes the small diff the
*right* small diff.

**In orchestration mode:** the conductor resolves material known ambiguities before writing the
brief. Workers do not interview the user — they state remaining assumptions and report newly
discovered unknowns or conflicts under Issues/Caveats for the conductor to follow up.
