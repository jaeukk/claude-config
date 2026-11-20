---
name: math-unicode
description: Use when a response needs mathematical notation (equations, filters, set-builder notation, statistics, calculus, linear algebra, logic, ratios, drops, counts) and the output goes to a terminal or TUI that cannot render LaTeX, such as Claude Code, Codex CLI, agy, SSH and tmux sessions, or CI logs. Load it before composing, including when the user explicitly mentions math-unicode. Emit Unicode glyphs inline, never raw LaTeX delimiters or commands. Do not use when the host renders math natively, such as ChatGPT or Codex on desktop and web, notebooks, or a KaTeX/MathJax target, and do not use when the user asked for LaTeX or a .tex file.
---

# math-unicode

In a terminal coding agent, write mathematics as inline Unicode. Never use LaTeX delimiters
(`$…$`, `\(…\)`, `$$…$$`, `\[…\]`) or commands: a terminal shows them raw.

## When it applies

Both must hold: the response carries mathematical notation, and the surface it lands on does not
render LaTeX (Claude Code, Codex CLI, agy and other terminal or TUI agents; anything read through
SSH, tmux, a pager or a CI log). No host tells a skill which surface it is on, so judge from
context; in a terminal coding agent, apply it.

Leave these alone:

- Output for a surface that renders math: ChatGPT and Codex on desktop and web, notebooks, any
  KaTeX or MathJax target.
- File content whose format has its own math convention. Obsidian notes and manuscripts take
  LaTeX (`research-note` requires it); this skill governs what is printed in the terminal.
- Math inside fenced code blocks, and strings passed to a tool that consumes LaTeX.
- Anything the user asked for as LaTeX or a `.tex` file. Give it, and say once that it will not
  render in the terminal.

## Style rules

Rule numbers are stable; `references/` cites them.

1. **Inline math:** Unicode, no delimiters. `The filter f(T; m) = { (s,r) : n_{s,r} ≥ m } produces
   the cohort.`
2. **Block math:** on its own line(s), still no delimiters. `|Q| / |T|  =  5 238 / 31 075  ≈  16.9 %`
3. **Subscripts.** A single index with a glyph takes the glyph (x₁, xᵢ, xₙ). A single index with
   no glyph takes a bare underscore (`I_ν`, `∂_μ`, `x_c`). A multi-character or grouped subscript
   takes `_{...}` or a plain suffix (`n_{s,r}`, `x_max`, `σ_obs`). Do not mix forms (`x_₁`) or
   brace an index that has a glyph (`x_{1}`).
4. **Superscripts.** A single exponent with a glyph takes the glyph (x², xⁿ, eˣ, A⁻¹, xᵀ). One
   with no glyph takes a bare caret (`x^ν`, `A^X`, `A^S`). A multi-character exponent takes caret
   plus parentheses, never `^{...}`: `x^(k+1)`, `x^(i)`, `e^(iπ)`.
5. **Big operators** take a bracketed inline selector, never `_{...}^{...}`: `..` for a range,
   `∈` for membership, `→` for a limit target, or a condition.

   ```
   ∑[i=1..n] aᵢ            ∏[k ∈ K] pₖ            ∫[a..b] f(x) dx
   ∫[−∞..∞] e^(−x²) dx     ⋃[i=1..n] Aᵢ           lim[x→0] f(x)
   ∑[m₁+...+mₙ=N] c_m      ∫[C] f(z) dz           Res[z=z₀] f(z)
   ```

   Named functions are ordinary letters (`Γ`, `erf`, `det`, `tr`, `Re`, `exp`, `argmin`), never
   `\operatorname`. A left-scripted function uses glyphs where they exist (`₂F₁(a,b;c;z)`) and
   readable ASCII otherwise (`_{p}F_q`).
6. **Fractions:** inline `a/b`, `(a + b) / (c + d)`. Stack one with a `─` rule only when it aids
   clarity.
7. **Matrices and vectors:** corner glyphs, and types declared in prose instead of faked bold or
   italic ("Here z and n are vectors, and Ω is a matrix.").

   ```
   A  =  ⎡ a  b ⎤        v  =  ( v₁ , v₂ , v₃ )ᵀ
         ⎣ c  d ⎦
   ```
8. **Tensor indices** are indices, not powers. Keep them ASCII and group only a multi-character
   index: `R^ρ_{σμν}`, `∂_μ`, `Γ^ρ_{νσ}`.
9. **Piecewise forms and aligned derivations:** brace glyphs `⎧ ⎩` for a short piecewise
   definition (a semicolon-separated sentence where a reader's font lacks them); equals signs in
   one column for a derivation.
10. **Sets:** set-builder with `:` or `|`.
    `Q  =  { (s,r) ∈ T  :  n_{s,r} ≥ 18  ∧  p⁰_{s,r} < 0.9 }`
11. **Numbers:** thin space (U+2009) for thousands (`5 238`), not commas; dot decimal; a space
    before `%` (`16.9 %`); `≈` or `∼` for approximation, `~` for order of magnitude; `x = 5.2 ± 0.3`.
12. **When Unicode hurts, fall back.** If a glyph chain is denser than the LaTeX it replaces,
    write readable ASCII and annotate it. No combining marks or obscure modifier letters to force
    a script; bare `^x` and `_x` are always acceptable, a wrong or missing glyph is not. Stay
    consistent within a passage.
13. **Plain letters for variables.** Never style letters with the Mathematical Alphanumeric
    Symbols block (U+1D400 onward: bold, italic, script, styled double-struck): those code points
    garble on copy, terminal search and screen readers. The number sets ℕ ℤ ℚ ℝ ℂ ℙ ℍ are the one
    exception (Letterlike Symbols, carried by terminal fonts). Double-struck E and F are in the
    banned block: write `E[X]` and `F`.

## Script glyphs: the portable set and its gaps

Which script glyphs exist, and which a terminal font draws, cannot be derived. Use only these.

```
superscript    ⁰ ¹ ² ³ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹  ⁺ ⁻ ⁼ ⁽ ⁾  ⁱ ⁿ ᵃ ᵇ ᶜ ᵈ ᵉ ᶠ ᵍ ʰ ʲ ᵏ ˡ ᵐ ᵒ ᵖ ʳ ˢ ᵗ ᵘ ᵛ ʷ ˣ ʸ ᶻ
sup (capital)  ᴬ ᴮ ᴰ ᴱ ᴳ ᴴ ᴵ ᴶ ᴷ ᴸ ᴹ ᴺ ᴼ ᴾ ᴿ ᵀ ᵁ ⱽ ᵂ
sup (Greek)    ᶿ                    (θ only; the rest are opt-in)
subscript      ₀ ₁ ₂ ₃ ₄ ₅ ₆ ₇ ₈ ₉  ₊ ₋ ₌ ₍ ₎  ₐ ₑ ₕ ᵢ ⱼ ₖ ₗ ₘ ₙ ₒ ₚ ᵣ ₛ ₜ ᵤ ᵥ ₓ
```

Do not invent or approximate a missing glyph. No code point exists for:

```
subscript letters      b c d f g q w y z    → x_b, x_c, x_d
subscript capitals     all 26               → A_B, M_N
superscript capitals   X Y Z                → A^X, M^Y, A^Z
superscript ∞          none                 → never ^∞ as a glyph; use ∫[a..∞]
Greek sub/superscript  μ ν σ and most rest  → I_ν, ∂_μ, x^ν
```

A code point exists but no measured monospace font ships it, so treat it as absent:

```
superscript S          U+A7F1, Unicode 17.0 → A^S
superscript C F Q      U+A7F2..U+A7F4       → A^C, A^F, A^Q
superscript q          U+107A5, outside BMP → x^q
Greek subscripts       ᵦ ᵧ ᵨ ᵩ ᵪ            → x_β, I_ρ, x_γ
Greek superscripts     ᵝ ᵞ ᵟ ᵠ ᵡ            → x^β, x^φ
```

Operators, Greek letters and digit scripts are drawn by every measured font, so prefer them.
Letter scripts are not: JetBrains Mono, Fira Code, Hack and IBM Plex Mono ship none, so `xᵢ` and
`xᵀ` arrive there through a fallback font at a different width, which breaks column alignment.
That is why `x_i` and `x^T` are first-class alternatives, and the better choice inside aligned
derivations and matrices. `ⱼ` and `ⱽ` are the thinnest-supported glyphs in the set; `x_j` and
`A^V` are equally fine. The missing superscript `∞` is why operator bounds are bracketed:
`∫₀^∞` would draw one bound as a glyph and the other as a caret.

## Glyphs to avoid

Each has a code point and looks right in a proportional editor, but at most one of the twelve
measured monospace fonts carries it, so a terminal draws it at the wrong width or as tofu.
Counts are `fonts carrying the glyph / fonts measured`.

| avoid | code point | fonts | write instead |
|---|---|---|---|
| ∮ | U+222E | 0/12 | `∫[C] f(z) dz` |
| ⅆ | U+2146 | 0/12 | `d` |
| ⅇ | U+2147 | 0/12 | `e` |
| ∖ | U+2216 | 0/12 | `A \ B` |
| ℵ | U+2135 | 0/12 | `aleph_0` |
| ℶ | U+2136 | 0/12 | `beth_0` |
| ⋘ | U+22D8 | 0/12 | `<<` |
| ⋙ | U+22D9 | 0/12 | `>>` |
| ⨁ | U+2A01 | 0/12 | `⊕[i=1..n]` |
| ⨂ | U+2A02 | 0/12 | `⊗[i=1..n]` |
| 〈 | U+3008 | 0/12 | `⟨` (U+3008 is a full-width CJK bracket) |
| 〉 | U+3009 | 0/12 | `⟩` |
| ≪ | U+226A | 1/12 | `<<` |
| ≫ | U+226B | 1/12 | `>>` |
| ⊨ | U+22A8 | 1/12 | `\|=` |
| ⊻ | U+22BB | 1/12 | `xor`, or `⊕` in a boolean-ring context |
| ⊼ | U+22BC | 1/12 | `nand` |
| ⊽ | U+22BD | 1/12 | `nor` |
| ⟸ | U+27F8 | 1/12 | `⇐` |
| ⟹ | U+27F9 | 1/12 | `⇒` |
| ⟺ | U+27FA | 1/12 | `⇔` |
| ⨅ | U+2A05 | 1/12 | `⊓[i=1..n]` |
| ⨆ | U+2A06 | 1/12 | `⊔[i=1..n]` |
| 𝔼 | U+1D53C | 1/12 | `E[X]` (also banned by Rule 13) |
| 𝔽 | U+1D53D | 1/12 | `F` (also banned by Rule 13) |
| 𝔸 | U+1D538 | 1/12 | `A`, or `ℕ ℤ ℚ ℝ ℂ` when a specific set is meant |

## Reference (read when needed)

- `references/glyph-tables.md`: the full glyph cheatsheet (Greek, operators, relations, logic,
  arrows, number sets), the per-font coverage table, a LaTeX to Unicode table, and worked hard
  forms (special functions, tensors, contour integrals, multiline output). Read it when unsure
  whether a common glyph is portable or how to set a difficult formula.
- `references/extended-glyphs.md`: the opt-in set for a font with wider coverage. Use it only when
  the user has asked for extended glyph coverage.
