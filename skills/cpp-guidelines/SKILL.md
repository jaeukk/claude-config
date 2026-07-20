---
name: cpp-guidelines
description: C++ coding standards for computational-physics work — C++23 idioms, RAII/ownership, Doxygen documentation for every function, numerical-code practices (units, floating-point, reproducibility), performance rules (algorithm choice, allocation-free hot loops, vectorization, OpenMP), code-reuse/anti-duplication rules, CMake and testing conventions. Use whenever writing, editing, reviewing, refactoring, optimizing, or deduplicating ANY C++ code (.cpp, .cc, .hpp, .h, CMakeLists.txt) or answering C++ build/compiler questions, even if the user doesn't explicitly say "C++" — e.g. "add a module", "speed up this simulation", "clean up this kernel", "fix this segfault".
license: MIT
---

# C++ Guidelines (C++23, computational physics)

Distilled from the [C++ Core Guidelines](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines)
and adapted for numerical-physics code. Complements `karpathy-guidelines` (behavioral
rules: simplicity, surgical changes) — this skill covers what the C++ itself should
look like. When editing an existing file, its local style wins over anything here.

## 1. Language level

Target **C++23**. Reach for the standard library before hand-rolling or adding a
dependency:

- `std::span<const T>` for non-owning array parameters (replaces `T*, size_t` pairs).
- `std::mdspan` for multidimensional views over flat storage.
- Ranges/views (`std::views::transform`, `filter`, `zip`, `enumerate`) for pipelines
  where they read better than an index loop — but a plain loop in a hot numerical
  kernel is fine and often clearer.
- `std::format` / `std::print` instead of `iostream` manipulators or `printf`.
- `std::numbers::pi`, `std::numbers::e`, … — never hand-typed constant literals.
- `std::expected<T, E>` for recoverable failures; exceptions for genuinely
  exceptional ones (see §5).
- `constexpr` for values computable at compile time (physical constants, table sizes).
- `enum class` over plain `enum`; `[[nodiscard]]` on functions whose return value is
  the whole point (nearly every pure computation); `noexcept` where it's true.

## 2. Ownership and resources (RAII)

- No raw `new`/`delete` in application code. `std::make_unique` for owned heap data,
  plain values or `std::vector` for almost everything else. `shared_ptr` only for
  genuinely shared lifetime, which is rare in simulation code.
- Rule of zero: let members manage themselves; write `= default` when you must
  declare a special member. If a class needs a real destructor, it's probably a
  resource wrapper — keep it tiny and single-purpose.
- Raw pointers/references as function parameters mean "borrowed, non-null unless
  documented" — never ownership transfer.

## 3. Interfaces and const-correctness

- `const` everything that doesn't mutate: parameters (`std::span<const double>`),
  methods, locals. Const-correctness is documentation the compiler checks.
- Pass cheap types (doubles, small structs, spans, views) by value; pass large
  objects by `const&`; take sink parameters by value and `std::move` them in.
- Headers: `#pragma once`; include what you use; order includes as
  matching-header, project, third-party, standard library.
- Keep functions focused enough that their Doxygen `@brief` is one honest sentence.

## 4. Documentation — Doxygen on every function

Every function gets a Doxygen comment (Javadoc style, `/** … */` with `@` commands).
Document **units and conventions** in `@param`/`@return` — silent unit mismatches are
the classic physics bug, and the comment is the only place the dimension lives.
Document each contract once: public functions in the header, internal helpers at
their definition — don't repeat the block at a `.cpp` definition whose header
already carries it.

Leave exactly **one blank line** between the end of a function and the next
function's doc-comment; no blank line between a doc-comment and its function.

**Never strip a documented parameter's name to silence `-Wunused-parameter`.**
`-Wunused-parameter` is a `-Wextra` diagnostic, *not* part of `-Wall`, so an
unused-but-named parameter does **not** warn under a normal `-Wall` build. Removing
the name (`f(const T&)`) — or commenting it out (`f(const T& /*x*/)`) — breaks the
Doxygen `\param x` match (Doxygen sees no parameter `x` and warns), so the name and
its documentation must stay in sync. Keep the name whenever a `\param` documents it,
and accept the `-Wextra`-only unused-parameter note as out of the normal-bar scope.
Only drop a parameter name where nothing documents it (e.g. a fixed-signature
callback lambda like the `IterateThroughNeighbors` visitor, which has no doc block).

```cpp
/**
 * @brief Radial distribution function g(r) of a 3D point pattern.
 * @param points    Particle positions in a cubic box, Cartesian [box units].
 * @param boxLength Side length of the periodic box [box units].
 * @param dr        Bin width of the histogram [box units].
 * @return Bin-averaged g(r), index i covering [i*dr, (i+1)*dr).
 */
[[nodiscard]] std::vector<double> radialDistribution(
    std::span<const Vec3> points, double boxLength, double dr);

/**
 * @brief Structure factor S(k) via direct summation.
 * ...
 */
```

Inside function bodies, comment only what the code cannot say: literature references
("Allen & Tildesley Eq. 6.24"), non-obvious algorithmic choices, unit conversions.

## 5. Errors

- Precondition violations (negative box length, empty input where physics requires
  points): `assert` in debug, or throw `std::invalid_argument` if the check must
  survive release builds.
- Recoverable conditions a caller should branch on (file missing, fit didn't
  converge): return `std::expected<T, E>`.
- Don't wrap numerical kernels in defensive try/catch — let errors propagate to the
  driver level where context exists to report them.

## 6. Numerical-physics specifics

- **Floating-point comparison:** never `==`. Compare with a relative tolerance
  scaled to the magnitudes involved, plus an absolute floor near zero.
- **Summation:** for long reductions where accuracy matters, prefer pairwise
  summation (or Kahan) over naive left-to-right accumulation; note the choice in a
  comment.
- **Cancellation:** watch for subtracting nearly equal quantities; algebraically
  reformulate (e.g. `std::hypot`, `expm1`, `log1p`) rather than hoping.
- **Data layout:** contiguous `std::vector<double>` + `mdspan` views beat
  vector-of-vectors; consider struct-of-arrays only for demonstrated hot loops.
- **Reproducibility:** seed RNGs explicitly (`std::mt19937_64 rng{seed}`), take the
  seed as a parameter, and record it in output. A simulation that can't be re-run
  is a simulation that can't be debugged.
- **Indices and sizes:** use `std::size_t` (or `ssize`/`std::ptrdiff_t` when
  differences appear); beware `-Wconversion` warnings — they catch real bugs in
  index arithmetic.
- **Loop-variable type follows the bound.** In a `for` loop, the counter and the
  variable it is compared against must be the **same type** — take the loop
  variable's type from the external variable that bounds it. Comparing against a
  `.size()` (a `std::size_t`) ⇒ declare the counter `std::size_t` and compare it
  directly: `for (std::size_t i = 0; i < v.size(); i++)`. Do **not** fix a
  `-Wsign-compare` by casting the bound to `int` (`i < (int)v.size()`) or, worse,
  by mixing a `size_t` counter with a `static_cast<int>` bound — that is
  inconsistent and doesn't fix the signedness. Keep a signed counter only for a
  genuinely signed loop (reverse `for (int i = n-1; i >= 0; --i)`), and then keep
  the whole loop signed. Be consistent across sibling loops in a file.

## 7. Performance

Algorithmic complexity first, micro-optimization last — and profile before tuning;
intuition about hot spots is wrong often enough to be expensive.

- **Complexity beats constants.** Cell/neighbor lists turn O(N²) pair loops into
  O(N); FFT-based estimators beat direct sums at large N. Pick the algorithm before
  polishing the loop — no amount of vectorization rescues the wrong complexity class.
- **No allocations in hot loops.** `reserve()` up front; reuse workspace buffers
  across calls (pass them in as spans) instead of reallocating per call; hoist
  temporaries out of inner loops.
- **Write vectorizable inner loops** — contiguous access, no branches or opaque
  function calls inside — and let the compiler do the SIMD. Verify with
  `-fopt-info-vec` (GCC) / `-Rpass=loop-vectorize` (Clang) when it matters, rather
  than hand-writing intrinsics.
- **Release flags:** `-O3 -DNDEBUG`, plus `-march=native` for runs on this machine
  and LTO for release builds. `-ffast-math` changes IEEE semantics (NaN handling,
  reassociation) — enable it only deliberately and note it in the build file.
- **Parallelism:** OpenMP or `std::execution::par` for independent iterations.
  Parallel reductions reorder floating-point sums, so results shift in the last
  digits — decide whether that's acceptable and say so where the pragma lives.

## 8. Reuse — don't write it twice

- **Library first.** Eigen / FFTW / BLAS-LAPACK / GSL and the standard library
  already implement linear algebra, FFTs, special functions, and RNG
  distributions — correct, tested, and faster than a rewrite. Hand-roll a
  numerical routine only when no established implementation fits.
- **Extract shared geometry/statistics helpers.** Minimum-image distance,
  coordinate wrapping, histogram binning, and tolerance comparison are the
  copy-paste magnets of simulation code; keep one inline version in a small
  shared header and use it everywhere, so a fix lands once.
- **Prefer a standard algorithm to a hand-rolled loop** (`std::ranges::fold_left`,
  `transform`, `max_element`, …) when equally clear — less code to review and the
  intent is in the name.
- **Templates on the second use, not the first.** Duplicated `float`/`double` or
  2D/3D versions of the same kernel are the signal to make it generic; a template
  written speculatively is complexity paid for nothing.
- **One home for constants and parameters** — a constants header or a parameters
  struct, never the same literal repeated across files.

## 9. Build and tooling

- CMake, target-based — no global flags:
  `target_compile_features(mylib PUBLIC cxx_std_23)`.
- Warnings on everywhere: `-Wall -Wextra -Wpedantic -Wconversion`
  (MSVC: `/W4 /permissive-`). Fix warnings; don't suppress them.
- Debug builds of new numerical code: run once with
  `-fsanitize=address,undefined` before trusting results — out-of-bounds indexing
  in a kernel usually corrupts silently instead of crashing.
- If the project has a `.clang-format`, run it; otherwise match the file.

## 10. Testing numerical code

Unit-test kernels against **physics, not just spot values**:

- analytic limiting cases (ideal gas → g(r) = 1, small-angle limits, known
  closed-form solutions);
- conserved quantities and symmetries (energy drift, momentum, parity of a
  correlation function);
- scaling behavior (halve `dr`, does the estimate converge as expected?).

A test that encodes a physical invariant survives refactoring; a test that pins a
magic number to 12 decimals breaks on the first legitimate change.

## Review checklist

Before calling C++ work done: every function has a Doxygen comment with units ·
blank-line spacing between functions · no raw `new`/`delete` · `const`/`[[nodiscard]]`
where they belong · no `==` on floats · RNG seeding explicit · no allocations inside
hot loops · no reimplemented library math · repeated geometry/binning logic extracted
into a shared helper, not copy-pasted · compiles warning-free at
`-Wall -Wextra -Wpedantic`.
