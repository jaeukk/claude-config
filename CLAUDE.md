# User & Identity

- **Name:** Jaeuk Kim, PhD — Physics postdoc.
- **Language:** Respond in English (American spelling) by default. Use Korean only when explicitly asked.

# Physics Coding Standards

Applies when writing computational-physics code (ported from Roo's `code` mode rules).

- Use **C++23** or **Python**.
- Use **pyMEEP** / **SMUTHI** for scattering simulations.
- Write **Doxygen-style** comments for each function.

# Research-code workflow — code vs generated data

Applies to every research-code repo (adopted 2026-08-04 in multi-stealthy after
a joint Claude+Codex assessment; boundary revised 2026-08-13). One rule:
**reusable code under git; single-use scripts and generated files live with the
data.**

- **Reusable code lives under git** — anything imported by other code, rerun with
  new inputs, or shared across datasets. Library modules at the repo root;
  exploratory work in a tracked `experiments/` directory. Commit WIP freely.
- **Single-use scripts live beside their data, unversioned.** A script stays in
  `20_Data/…` only if **all** of these hold: it is *one* human-authored file (the
  dataset it reads and the figures it emits don't count); it reads *one* dataset
  directory; and it has no generic input interface, parameter sweep, driver
  script, or companion code/config. Resolve paths relative to the dataset
  directory instead of hardcoding them (Python: `Path(__file__).resolve().parent`).
  Fail any clause → it belongs in the repo. The test is deliberately about
  observable properties, not about whether it "feels simple" or you intend to run
  it once — intent isn't decidable when you write the file.
- **Generated outputs (PNG, `.pkl`, logs, result JSON/CSV) never go into a
  repo.** Scripts write to the project's data area (e.g.
  `20_Data/<topic>/YYYY-MM-DD-name/`); put the output path as a constant at the
  top of the script. A project's own `CLAUDE.md` records its canonical repo and
  topic routing.
- **Imports via editable install** (`pip install -e`, minimal `pyproject.toml`)
  — never `sys.path` hacks, never copy a library module to tweak it. This applies
  to dataset-local scripts too: being unversioned buys them no exemption.
- **Experiment layout:** exploratory work with more than one human-authored
  code/config file, or intended for repeated/parameterized runs, gets
  `experiments/YYYY-MM-DD_name/` with a 3-line `README.md` (question, run
  command, library commit hash from `git rev-parse --short HEAD`), the run
  script/notebook, and config. Input datasets and generated outputs do **not**
  count as companion files.
- **Promotion:** *move* (never copy) a matured function into the library module,
  or a whole data-local script into `experiments/`; add a small test, update the
  imports, record the new commit hash in the experiment README. **Before a
  one-off's output is published, cited, or depended on downstream, promote and
  commit the script first** — that is what replaces the commit-hash provenance an
  unversioned script cannot carry.
- **Retiring:** old experiments stay (code is cheap, it's a lab notebook);
  sweep clutter into `experiments/_archive/` with `git mv`, don't delete.
- `.gitignore` is directory-first (`__pycache__/`, `build/`, `temp/`,
  `evidence/`, `*.pkl`, `*.log`, `*.so`); no global `*.png`/`*.json` ban —
  fixtures and doc figures stay trackable.

# Ponytail precedence

The `ponytail` plugin injects an always-on "laziest solution that works" ladder
(YAGNI → reuse → stdlib → native → installed dep → one line → minimum). Where it
meets the rules above:

- **Documentation is not boilerplate.** The Doxygen comment per C++ function required
  above — and the NumPy-style docstrings the `python-guidelines` skill requires — are
  explicitly requested, so they fall under ponytail's own "never simplify away
  anything explicitly requested."
- **Scope is code.** Ponytail does not govern Obsidian research notes, manuscripts,
  literature summaries, or Korean prose.
- **Ambiguity still gets a question.** Ponytail's "never stall, ship the lazy
  version" governs *solution scope*; genuine *requirement* ambiguity — two readings
  leading to materially different work — still gets asked about first.
- **`/ponytail-review` vs `/simplify`.** ponytail-review reports an over-engineering
  delete-list and changes nothing; `/simplify` applies fixes. Neither replaces
  `/code-review`, which hunts bugs.

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

# HPC — KISTI Nurion

SSH hosts in `~/.ssh/config`: **`nurion`** (nurion.ksc.re.kr) and **`nurion-dm`**
(nurion-dm.ksc.re.kr, data-mover). User `e1837a01`. Both use `ControlMaster auto`
+ `ControlPath ~/.ssh/controlmasters/%r@%h:%p` + `ControlPersist 10m`.

**The two hosts have strictly disjoint roles — neither substitutes for the other:**

- **File I/O** (`scp`/`rsync`, all data movement) works **only through `nurion-dm`**.
  Do *not* route transfers through `nurion` even though `/scratch` is the same
  shared filesystem — the compute-login host may block or throttle them.
- **Job submission and scheduler queries** (`qsub`, `qstat`, `qdel`) work **only
  through `nurion`**. Do not attempt them on `nurion-dm`.
- A task that both submits a job *and* moves its output therefore needs **both**
  sockets live; each is opened separately by the user and each has its own socket
  file — having one does **not** cover the other.

**Auth requires an interactive OTP** (keyboard-interactive), which I cannot supply.
Non-interactive use works only while the user has a live master socket. Check with
`ssh -O check nurion` before any remote command. If it is dead, **ask the user to
open it** (`ssh nurion` / `ssh nurion-dm`, enter OTP, leave it open) — do **not**
retry ssh repeatedly. Repeated failures (`ssh_askpass: … No such file or
directory`, `Too many authentication failures`) risk **account lockout**; stop
after the first failure. Ping `ssh nurion true` every ~3 min to keep a socket warm
during long sequences.

Other recurring traps:

- **`scp`**: the default SFTP-based scp fails with `Connection closed`. Use the
  legacy protocol: `scp -O …`.
- **Scheduler is PBS Pro, not Slurm** — `qsub`, never `sbatch`. Job IDs look like
  `23342240.pbs`; scripts carry `#PBS` directives.
- **PBS `.e`/`.o` files are usually empty** (`-k eo` plus a redirect); the real
  Python/meep traceback is in the working directory as `./*_o.log`.
- `qstat -x` history is purged quickly. For a job it no longer knows, fall back to
  the Gmail **`HPC notices/Nurion`** label — query by label *display name*, since
  the label-ID form returns nothing.

# Common Python modules (`~/30_Codes/python/Common/`)

Reusable utilities live in `/home/jaeukk/30_Codes/python/Common/`. Install it
editable once (`pip install -e /home/jaeukk/30_Codes/python/Common`), then
`import` by module name from anywhere — no `sys.path` hacks, per the workflow
rule above.

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
