@~/.claude/multiagent/global-instructions.md
@~/.claude/research-conventions.md

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
