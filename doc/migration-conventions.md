# Migration and identity conventions: mapping `fim` to the literature

- [Who this document is for](#who-this-document-is-for)
- [1. `fim`'s own four conventions](#1-fims-own-four-conventions)
- [2. Per-paper mappings](#2-per-paper-mappings)
  - [2.1 Ryman & Leimar (2008)](#21-ryman--leimar-2008)
  - [2.2 Crow & Aoki (1984)](#22-crow--aoki-1984)
- [3. Why this document exists](#3-why-this-document-exists)
- [Metadata](#metadata)

## Who this document is for

Anyone comparing `fim`'s own output — a simulated run, or the exact
gene-identity recursion `test/validation/test_simulator_equilibrium.py`
implements — against a published population-genetics paper. Every
comparison of this kind needs a mapping between two independently
chosen sets of conventions before the numbers mean the same thing; get
the mapping wrong, or skip it, and an entirely correct simulator and an
entirely correct paper can disagree by up to 58% while both sides are
right (§2.1, below). This document exists so that mapping is derived
once, here, rather than re-derived silently inside one test's own
docstring each time — the arrangement that let the
[Crow & Aoki discrepancy](fim-simulator-test-plan.md#appendix-a-open-issues)
go unexplained for as long as it did.

## 1. `fim`'s own four conventions

Four independent choices, each of which some cited paper makes
differently. None is more "correct" than its alternative; they are
different, equally valid ways to formalize the same biology.

1. **Population unit.** `N` is the gene-copy count per deme (`doc/
   configuration.md#n`) — for a diploid autosomal locus, twice the
   census individual count. A paper that states its own parameter as
   an individual count, not a gene-copy count, needs a factor of 2
   applied before any other mapping below.
2. **Migration source.** `fim`'s migration blend sends a deme's own
   redrawn fraction `m` to the *other* `d − 1` demes only — `(1 − m)`
   on the diagonal, `m / (d − 1)` off it (`doc/configuration.md#m`).
   Some papers (Nei 1975; Li 1976; Ryman & Leimar 2008) instead redraw
   `m` from a pool that includes the home deme itself.
3. **Identity sampling.** `fim` tracks `Σ p²` — the with-replacement
   probability that two copies drawn from a deme match, which counts
   drawing the same physical copy against itself. Some papers (Ryman &
   Leimar's own `J`) define identity as the probability two *distinct*
   copies match, sampling without replacement.
4. **Operator order.** `fim` composes one generation as
   Migrate → Mutate → Drift (`doc/fim-simulator-design.md#34-the-generation-update-pipeline`).
   A paper's own recursion may be written in a different order.

## 2. Per-paper mappings

### 2.1 Ryman & Leimar (2008)

Derived and numerically verified in an internal design-analysis note
(`20260903-claude-opus-5-gene-identity-recursion-fim-implications.md`,
§3.3 — a private companion document, not part of this repository; the
mapping itself is fully restated here so this reference does not
depend on it) against a grid of 240 `(N, d, m, u)` combinations, 714
sampled generations each. All three conventions above apply (operator
order does not, once identity sampling is mapped — see the third row):

| `fim`'s convention | Ryman & Leimar's convention | Mapping |
|---|---|---|
| Migration source: other `d − 1` demes | Migration source: pool including self | `m_fim = m_paper · (d − 1) / d`; `m_paper = m_fim · d / (d − 1)` |
| Identity: with replacement, `Σ p²` | Identity: distinct pairs only | `Gs_paper = (N · Gs_fim − 1) / (N − 1)`; `Gd` needs no conversion (two copies from different demes are always distinct) |
| Migrate → Mutate → Drift | Drift → Migrate → Mutate (as written) | No lag needed — the with-replacement-to-distinct conversion above already accounts for the phase difference exactly (verified: no generation offset improves agreement) |

`fim`'s own mutation factor, `(1 − u)² + u(1 − u)/N` (the exact second
moment of its own binomial-count mutation operator), differs from
Ryman & Leimar's `(1 − u)²` (per-lineage infinite-alleles mutation) by
`u(1 − u)/N` — a real difference between two different, both correct,
mutation models, not a convention gap to close. Documented and
tolerated, not corrected: at most 0.11% residual on `G_ST` and 0.45% on
`D` across the full grid, negligible next to the migration and identity
corrections above (which resolve an *uncorrected* discrepancy of up to
58% down to about 0.1%).

Migration source's own correction is largest at small `d` — 11% at
`d = 10`, a factor of 2 at `d = 2` — which is why an unmapped
comparison is catastrophic at small `d` and only misleading at large
`d`. Identity sampling's own correction is `O(1/N)` — negligible at
equilibrium with large `N`, but proportionally largest in the first
few dozen generations, when identity is still near 1 (measured: 22%
relative gap on `G_ST` uncorrected, at `N = 2000, d = 10, u = 10⁻⁴,
t = 10`) — exactly the transition-phase region a trajectory comparison
cares about most.

### 2.2 Crow & Aoki (1984)

**Unresolved.** Crow & Aoki's own stepping-stone (torus) scenario
reports `G_ST = 0.172`; `fim`'s exact recursion, under `fim`'s own
migration-source convention, lands at `G_ST ≈ 0.324`
(`test_pairwise_identity_recursion_applied_to_the_crow_aoki_torus`).
Applying the *same* "pool includes self" mapping that resolved §2.1
above — spread over the home deme's own four spatial neighbors instead
of the whole population — moves the number *further* from `0.172`, to
about `0.374`
(`test_crow_aoki_torus_under_the_papers_own_migration_convention`),
ruling out that specific hypothesis rather than confirming it. Crow &
Aoki's own paper states its stepping-stone numbers came from
unpublished "numerical calculations," with no formula given for
exactly how a deme's migrant count is divided among its neighbors —
whatever convention they actually used remains unidentified. See
[the test plan's own Appendix A](fim-simulator-test-plan.md#appendix-a-open-issues)
for the full record.

## 3. Why this document exists

Every literature comparison in this project used to re-derive its own
mapping implicitly, in prose, inside one test's own docstring — the
exact arrangement under which the Crow & Aoki discrepancy went
unexplained, and under which a Ryman & Leimar comparison built without
this document would have gone wrong the same way (an unmapped
comparison disagrees by up to 58%; §2.1's own table resolves that to
about 0.1%). New literature comparisons should add a row here rather
than re-deriving the mapping locally.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-04
generator-responsibility: design
```
