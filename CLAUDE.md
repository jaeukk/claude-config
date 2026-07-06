# User & Identity

- **Name:** Jaeuk Kim, PhD — Physics postdoc.
- **Language:** Respond in English by default. Use Korean only when explicitly asked.

# Physics Coding Standards

Applies when writing computational-physics code (ported from Roo's `code` mode rules).

- Use **C++23** or **Python**.
- Use **pyMEEP** / **SMUTHI** for scattering simulations.
- Write **Doxygen-style** comments for each function.

# Research notes — Obsidian vault folder convention

Each project keeps its notes in an Obsidian-vault **Notes root** `<root>`. Resolve
the vault base with the **`zotero-obsidian-sync`** skill — the Windows
user-profile segment varies per machine, so never hardcode it. A project's own
`CLAUDE.md` records its specific `<root>` (and any Zotero collection); the
subfolder layout below is global and need not be repeated per project.

Under every `<root>`:

- `10_ResearchNotes/` — daily research notes, one file per date `YYYY-MM-DD.md`
  (zero-padded), filed in **either** `01_NotConfirmed/` (active work — check
  first) **or** `02_Confirmed/` (validated), not both; figures/attachments go in
  `_assets/`. Don't assume a note exists for every date (only working days).
  When inserting into an existing note, **do not** add new YAML frontmatter or a
  top-level `#` heading — use `##`/`###` subheadings and LaTeX math
  (`$inline$` / `$$display$$`).
- `20_Progress/` — compiled / progress notes
- `30_Literature/` — literature-search results
- `40_Manuscript/` — manuscript-related notes
- `ResearchPlan.md` — overall research plan and pomodoro tasks

# Zotero

Zotero **is writable**, despite the local REST API (`localhost:23119/api`) being
read-only (`POST` → `400 "Endpoint does not support method"`). Add items via the
**Connector** `POST /connector/saveItems` into the currently-selected collection
— full recipe in the **`zotero-obsidian-sync`** skill. Don't conclude "can't write."

# Common Python modules (`~/30_Codes/python/Common/`)

Reusable utilities live in `/home/jaeukk/30_Codes/python/Common/`. Run from that
directory or add it to `sys.path`, then `import` by module name.

- **`prop_uncertainty.py`** — propagation of uncertainty (Wikipedia: *Propagation
  of uncertainty*) for an arbitrary scalar- or vector-valued `f`. First-order
  (linear) **and** Monte-Carlo estimators; numerical Jacobian by central finite
  differences, so **only `f` is needed** (no analytic gradient). Supports
  correlated inputs and a per-parameter variance budget. numpy-only.

  ```python
  from prop_uncertainty import propagate_uncertainty, monte_carlo_uncertainty
  f = lambda I, R: I**2 * R                       # any f(x1, x2, ...)
  r = propagate_uncertainty(f, x=[0.2, 100], u=[0.01, 3.0])
  r.value, r.std                                  # 4.0, 0.418  (nominal, combined std)
  print(r.summary(["I", "R"]))                    # value ± std + variance budget
  # correlations:  corr=<n×n>  or  cov=<n×n>      # full input covariance
  # vector-valued f -> r.covariance is the output covariance matrix
  # nonlinear cross-check:  monte_carlo_uncertainty(f, x, u, random_state=0)
  ```
