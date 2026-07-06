---
name: python-guidelines
description: Python coding standards for computational-physics work — modern Python (3.11+) with type hints, NumPy-style docstrings with units on every public function, NumPy/SciPy vectorization and floating-point practices, reproducible RNG, performance rules (profile first, complexity before micro-optimization, numba/C++ as escalation), code-reuse rules, and uv/ruff/pytest tooling. Use whenever writing, editing, reviewing, refactoring, optimizing, or deduplicating ANY Python code (.py, .ipynb, pyproject.toml) — simulation and analysis scripts, pyMEEP/SMUTHI scattering code, plotting, data processing — even if the user doesn't say "Python" explicitly, e.g. "speed up this script", "clean up my analysis", "add a g(r) function".
license: MIT
---

# Python Guidelines (scientific Python, physics)

Distilled from PEP 8, the [numpydoc standard](https://numpydoc.readthedocs.io/en/latest/format.html),
and scientific-Python community practice (including probabl's python-code-style
skill), adapted for numerical-physics work. Complements `karpathy-guidelines`
(behavioral rules) and mirrors `cpp-guidelines` — the two halves of the same
dual-language codebase convention. When editing an existing file, its local style wins.

## 1. Language level

Target **Python ≥ 3.11**. Modern, typed, standard-library-first:

- Type-hint public function signatures with builtin generics — `list[float]`,
  `dict[str, NDArray]`, `float | None` — so the contract is machine-checkable.
  `numpy.typing.NDArray` for arrays.
- `@dataclass(slots=True)` for parameter bundles and results instead of loose
  dicts/tuples — named fields are self-documenting and typo-proof.
- `pathlib.Path` everywhere, never `os.path` string surgery.
- f-strings; `enumerate`/`zip` instead of `range(len(...))`; `with` context
  managers for files and resources.
- Comprehensions when they fit on a line or two; a plain loop once nesting or
  conditions make the comprehension a puzzle.

## 2. Environment — uv

- Run and manage everything through **uv**: `uv run script.py`, `uv add numpy`,
  `uv run pytest`. Bare `python` is not guaranteed on PATH (notably on this
  Windows setup) and bypasses the project environment.
- Project dependencies live in `pyproject.toml`. For a standalone analysis
  script, use PEP 723 inline metadata (`# /// script … dependencies = […] # ///`)
  so `uv run` resolves it without a project.

## 3. Docstrings — NumPy style, units mandatory

Every public function gets a **NumPy-style docstring**; underscore-private helpers
may make do with a one-line summary. Document **units and conventions** in
`Parameters`/`Returns` — silent unit mismatches are the classic physics bug, and
the docstring is the only place the dimension lives.

```python
def radial_distribution(
    points: NDArray, box_length: float, dr: float
) -> NDArray:
    """Radial distribution function g(r) of a 3D point pattern.

    Parameters
    ----------
    points : (N, 3) ndarray
        Particle positions in a cubic periodic box, Cartesian [box units].
    box_length : float
        Side length L of the periodic box [box units].
    dr : float
        Histogram bin width [box units]; 0 < dr <= L/2.

    Returns
    -------
    (n_bins,) ndarray
        Bin-averaged g(r), bin i covering [i*dr, (i+1)*dr); g -> 1 for an
        ideal gas.

    Raises
    ------
    ValueError
        If fewer than two points are given or dr is outside (0, L/2].
    """
```

Inside function bodies, comment only what the code cannot say: literature
references ("Allen & Tildesley Eq. 6.24"), non-obvious algorithmic choices,
unit conversions.

## 4. NumPy and floating point

- **Vectorize.** No Python-level loops over array elements — use broadcasting,
  fancy indexing, `einsum` for contractions. A loop over *configurations* or
  *files* is fine; a loop over *particles* usually is not.
- **Never grow arrays in a loop** (`np.append`/`np.concatenate` per iteration is
  quadratic). Preallocate, or collect in a list and `np.stack` once at the end.
- **Views vs copies:** basic slicing returns views — mutate deliberately; use
  `.copy()` when the original must survive.
- **No `==` on floats.** `np.isclose`/`np.allclose` (or `math.isclose`) with
  explicitly chosen `rtol`/`atol`; in tests, `np.testing.assert_allclose`.
- **Cancellation:** reach for `np.expm1`, `np.log1p`, `np.hypot` instead of the
  naive expressions they replace. For precision-critical scalar sums use
  `math.fsum` (`np.sum` is already pairwise, which usually suffices).
- **dtype discipline:** `float64` by default; don't let a stray `float32` array
  silently downgrade a pipeline. Constants from `numpy.pi` / `scipy.constants`,
  never retyped literals.
- **Reproducibility:** `rng = np.random.default_rng(seed)` with the seed as a
  parameter, recorded in output — never the legacy global `np.random.seed`.
  A simulation that can't be re-run can't be debugged.

## 5. Performance

Profile before tuning (`cProfile`, `line_profiler`, `%timeit`) — then fix the
algorithm before the constant factor:

- **Complexity beats constants.** `scipy.spatial.cKDTree` or cell lists for
  neighbor queries instead of all-pairs; `scipy.fft` for O(N log N) estimators.
- **Escalation ladder:** clean NumPy vectorization → `numba.njit` for a kernel
  that is irreducibly iterative → port the kernel to C++ (see `cpp-guidelines`)
  when numba isn't enough. Don't jump a rung early — each step costs
  maintainability.
- **Memory:** avoid chains of large temporaries in hot paths — use in-place
  operators or `out=` arguments where readability survives.
- **Parallel parameter sweeps:** `joblib.Parallel` or `multiprocessing` for
  embarrassingly parallel runs; remember each worker needs its own seeded
  `default_rng` (seed + worker id).

## 6. Reuse — don't write it twice

- **Library first.** NumPy/SciPy already implement special functions,
  integration, optimization, statistics, spatial queries, FFTs; scattering
  simulations go through pyMEEP / SMUTHI. Hand-roll only when nothing fits.
- **Extract the copy-paste magnets** — minimum-image distance, wrapping,
  binning, tolerance comparison — into one shared module so a fix lands once.
- **One home for constants and parameters** — a constants module or a config
  dataclass, never the same literal repeated across scripts.
- **Scripts get a `main()`** behind `if __name__ == "__main__":` with `argparse`
  for inputs — importable for reuse and testing, runnable standalone.

## 7. Errors

- Bad physics input (negative box length, empty configuration): raise
  `ValueError` with the offending value in the message.
- Never bare `except:`; catch the narrowest exception you can actually handle,
  and let the rest propagate to the driver level where context exists.
- `assert` for internal invariants only — asserts vanish under `-O`, so they
  must never guard user input.

## 8. Lint, format, type-check

- **ruff is the single style tool** (replaces black/isort/flake8). After touching
  Python files: `uv run ruff format <files>`, then `uv run ruff check --fix
  <files>`, then `uv run ruff check <files>`. If a warning survives two passes,
  surface it instead of looping.
- Only lint code you touched — not vendored or generated files.
- Type-check with pyright/mypy when the project already does; don't bolt it onto
  a quick analysis script.

## 9. Testing numerical code

`pytest`, and test kernels against **physics, not just spot values**:

- analytic limiting cases (ideal gas → g(r) = 1, known closed forms);
- conserved quantities and symmetries;
- convergence behavior (halve `dr`, does the estimate converge as expected?);
- `np.testing.assert_allclose` with explicit tolerances; seed every RNG.

A test that encodes a physical invariant survives refactoring; a test that pins
a magic number to 12 decimals breaks on the first legitimate change.

## Review checklist

Before calling Python work done: every public function has a NumPy-style
docstring with units · type hints on public signatures · no Python loops over
array elements · no array growth inside loops · no `==` on floats ·
`default_rng` with explicit seed · no reimplemented NumPy/SciPy functionality ·
repeated helpers extracted into a shared module · ruff format + check clean ·
run via `uv run`.
