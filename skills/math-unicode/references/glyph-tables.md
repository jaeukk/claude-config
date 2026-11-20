# Glyph tables, font coverage and worked forms

Moved verbatim from SKILL.md (2026-11-20) to keep the skill's core short. Rule and section names
in this file ("Rules 3 and 4", *Glyphs to avoid*) refer to SKILL.md. The measuring script and test
suite mentioned under *Font coverage* belong to the upstream repository and are not shipped in this
copy.

Measured coverage of the portable script set: 17/26 lowercase subscripts, 0/26 subscript
capitals, and 19/26 superscript capitals have a code point that some monospace font ships.

## Glyph cheatsheet

### Greek

```
lowercase   α β γ δ ε ζ η θ ι κ λ μ ν ξ ο π ρ σ τ υ φ χ ψ ω
uppercase   Α Β Γ Δ Ε Ζ Η Θ Ι Κ Λ Μ Ν Ξ Ο Π Ρ Σ Τ Υ Φ Χ Ψ Ω
variants    ϵ ϑ ϕ ϖ ϱ ς
```

### Operators

```
arithmetic     + − × ÷ ± ∓ · ∗ ⋅ ∘ ⊕ ⊖ ⊗ ⊘ ⊙
big            ∑ ∏ ∐ ∫ ∬ ∭
roots          √ ∛ ∜
calculus       ∂ ∇ Δ ∆
constants      ∞ ∅
```

### Relations

```
equality       =  ≠  ≈  ≅  ≡  ≜  ≝  ≐  ∝  ∼  ≃  ≢
order          <  >  ≤  ≥  ⊴  ⊵
set            ∈ ∉ ∋ ∌  ⊂ ⊃ ⊆ ⊇ ⊊ ⊋  ⊏ ⊐ ⊑ ⊒
set ops        ∪ ∩ ⊎ ⊔ ⊓        (set difference: A \ B)
```

### Logic & arrows

```
logic          ∧ ∨ ¬ ⊕   ⊢ ⊥ ⊤
quantifiers    ∀ ∃ ∄ ∴ ∵
arrows         → ← ↔ ⇒ ⇐ ⇔ ↦ ↪ ↩ ↑ ↓ ⇑ ⇓ ⟶ ⟵ ⟷ ⊸
```

### Number sets & brackets

```
sets           ℕ ℤ ℚ ℝ ℂ ℙ ℍ
brackets       ⟨ ⟩  ⌈ ⌉  ⌊ ⌋  ‖ ‖
```

## Font coverage

Unicode says a code point exists. Whether a terminal draws it is a property of
the font. These counts come from reading the `cmap` table of twelve monospace
fonts: the six most widely installed programming fonts (JetBrains Mono, Fira
Code, Cascadia Code, Hack, Source Code Pro, IBM Plex Mono) and six system fonts
(DejaVu Sans Mono, Liberation Mono, Ubuntu Mono, Ubuntu Sans Mono, Noto Mono,
Cousine). `scripts/measure-font-coverage.mjs` regenerates them and the test
suite asserts every number below against the result.

| what | glyphs | fonts |
|---|---|---|
| core operators and relations | ∑ ∏ ∫ √ ∂ ∞ ± × ÷ · ≈ ≠ ≤ ≥ ¬ − ² ³ | 12/12 |
| Greek letters | α β γ δ θ λ μ π σ φ ω Γ Δ Θ Λ Σ Φ Ω | 11-12/12 |
| sub/superscript digits | ⁰ ¹ ² ³ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹ ₀ ₁ ₂ ₃ ₄ ₅ ₆ ₇ ₈ ₉ | 9-12/12 |
| arrows, single | → ← ↔ ↑ ↓ | 9/12 |
| set and logic symbols | ∈ ∉ ∪ ∩ ⊂ ⊆ ∧ ∨ ∀ ∃ ∅ ∇ | 4-8/12 |
| superscript letters, lowercase | ᵃ ᵇ ᶜ ᵈ ᵉ ᶠ ᵍ ʰ ʲ ᵏ ˡ ᵐ ᵒ ᵖ ʳ ˢ ᵗ ᵘ ᵛ ʷ ˣ ʸ ᶻ | 4-5/12 |
| superscript i and n | ⁱ ⁿ | 2-7/12 |
| superscript theta | ᶿ | 4/12 |
| superscript signs and parens | ⁺ ⁻ ⁼ ⁽ ⁾ | 3-4/12 |
| subscript signs and parens | ₊ ₋ ₌ ₍ ₎ | 3-4/12 |
| superscript letters, capital | ᴬ ᴮ ᴰ ᴱ ᴳ ᴴ ᴵ ᴶ ᴷ ᴸ ᴹ ᴺ ᴼ ᴾ ᴿ ᵀ ᵁ ⱽ ᵂ | 1-3/12 |
| subscript letters, wider half | ₐ ₑ ᵢ ₒ ᵣ ᵤ ᵥ ₓ | 3/12 |
| subscript letters, thin half | ₕ ₖ ₗ ₘ ₙ ₚ ₛ ₜ ⱼ | 1/12 |
| number sets | ℕ ℤ ℚ ℝ ℂ ℙ ℍ | 3/12 |
| arrows, double | ⇒ ⇐ ⇔ ⇑ ⇓ | 4/12 |
| multiline brackets | ⎡ ⎣ ⎤ ⎦ ⎧ ⎩ ⟨ ⟩ | 4/12 |

Read that table as a ranking, not a verdict. A glyph the primary font lacks is
usually supplied by fontconfig from another installed font, so it still draws,
but at a different advance width, which is what breaks column alignment in
aligned derivations and matrices. Only a glyph no installed font carries shows
as tofu.

Two consequences worth acting on:

- The operators, Greek letters and digit scripts survive everywhere. Prefer them,
  and they carry most of the load in ordinary output.
- The letter sub/superscripts are DejaVu-class. JetBrains Mono, Fira Code, Hack
  and IBM Plex Mono ship none of them, so `xᵢ` and `xᵀ` arrive through fallback
  there. That is the reason Rules 3 and 4 keep `x_i` and `x^T` as first-class
  alternatives rather than last resorts.
- `ⱼ` and `ⱽ` are the two thinnest glyphs in the portable blocks, carried by one
  font each. They sit in the ranges above rather than in *Glyphs to avoid*
  because their fallback, `x_j` and `A^V`, is already what Rules 3 and 4 say to
  write when in doubt.

### Common LaTeX → Unicode

| LaTeX | Unicode | LaTeX | Unicode | LaTeX | Unicode |
|---|---|---|---|---|---|
| `\alpha` | α | `\sum` | ∑ | `\in` | ∈ |
| `\beta` | β | `\prod` | ∏ | `\notin` | ∉ |
| `\gamma` | γ | `\int` | ∫ | `\subset` | ⊂ |
| `\delta` | δ | `\partial` | ∂ | `\subseteq` | ⊆ |
| `\epsilon` | ε | `\nabla` | ∇ | `\cup` | ∪ |
| `\theta` | θ | `\infty` | ∞ | `\cap` | ∩ |
| `\lambda` | λ | `\emptyset` | ∅ | `\setminus` | `\` |
| `\mu` | μ | `\leq` | ≤ | `\wedge` | ∧ |
| `\pi` | π | `\geq` | ≥ | `\vee` | ∨ |
| `\sigma` | σ | `\neq` | ≠ | `\neg` | ¬ |
| `\phi` | φ | `\approx` | ≈ | `\Rightarrow` | ⇒ |
| `\omega` | ω | `\equiv` | ≡ | `\Leftrightarrow` | ⇔ |
| `\sqrt` | √ | `\propto` | ∝ | `\forall` | ∀ |
| `\pm` | ± | `\cdot` | · | `\exists` | ∃ |
| `\times` | × | `\to` | → | `\mathbb{R}` | ℝ |

## Quick reference — common forms

```
Mean / std        μ ± σ                        x̄ ± s
Probability       P(A | B)                     ℙ(A ∩ B) = ℙ(A) · ℙ(B | A)
Expectation       E[X] = ∫ x · f(x) dx
Variance          Var(X) = E[X²] − E[X]²
Gradient          ∇f = ( ∂f/∂x₁ , ... , ∂f/∂xₙ )
Norm              ‖x‖₂ = √(∑[i=1..n] xᵢ²)
Big-O             T(n) = O(n log n)
Limit             lim[n → ∞] aₙ = L
Sum bounds        ∑[i=1..n] i  =  n(n+1)/2
Quantile          q_α = inf{ x : F(x) ≥ α }
```

## Golden corpus — difficult terminal-native forms

These examples are deliberately chosen to exercise non-mappable indices,
constrained sums, tensors, special functions, contours, and multiline output.
They are normalized terminal forms of standard formulas (including DLMF
§10.32.E2, §15.6.E1, §19.19.E1, and §21.2.E1).

```
I_ν(z) = (z/2)^ν / (√π Γ(ν+½)) ∫[0..π] e^(±z cos θ)(sin θ)^(2ν) dθ

F(a,b;c;z) = 1 / (Γ(b)Γ(c−b)) ∫[0..1] t^(b−1)(1−t)^(c−b−1) / (1−zt)^a dt

T_N(b,z) = ∑[m₁+...+mₙ=N] ((b₁)_{m₁} ··· (bₙ)_{mₙ}) / (m₁! ··· mₙ!) · z₁^(m₁) ··· zₙ^(mₙ)

θ(z | Ω) = ∑[n ∈ ℤ^g] exp(2π i(½ n · Ω · n + n · z))
  Here z and n are vectors, and Ω is a matrix.

R^ρ_{σμν} = ∂_μ Γ^ρ_{νσ} − ∂_ν Γ^ρ_{μσ} + Γ^ρ_{μλ} Γ^λ_{νσ} − Γ^ρ_{νλ} Γ^λ_{μσ}

f^(n)(z₀) = n! / (2π i) ∫[C] f(z) / (z−z₀)^(n+1) dz

∂u/∂t + (u · ∇)u = −∇p + νΔu + f,  ∇·u = 0

p(x) = exp(−½ (x−μ)ᵀΣ⁻¹(x−μ)) / √((2π)ᵈ det Σ)

F(ω) = ∫[−∞..∞] f(t)e^(−iωt) dt

f(x) =
  ⎧ x²    if x ≥ 0
  ⎩ −x    if x < 0
```
