# PCG residual certification and solution accuracy

CMG certifies the residual against the original submitted system. For a returned
solution `x`, the accepted threshold is

```text
absolute_tolerance
  + relative_tolerance * (||b||₂ + operator_norm_bound * ||x||₂).
```

`PcgOptions::relative_tolerance` controls this backward-residual expression.
`PcgDiagnostics::relative_residual()` separately reports `||b - Lx||₂ / ||b||₂`.
The default relative tolerance is `1e-8`, the absolute tolerance is zero, and
candidate convergence always receives a fresh original-system residual check.

Solution error also depends on conditioning. After fixing the component means,
an error along an eigenvector with a small positive eigenvalue can be large
while its residual is small. An upper bound on the operator norm helps compute
the residual threshold; a forward-error bound would also need information about
the smallest positive eigenvalues. The current certificate does not estimate
those eigenvalues or promise a fixed relative error in the coefficients.

The weighted-path investigation provides a concrete example. Its long component
has 65,533 vertices and edge weights from 0.001 to 1,000. At the default setting,
the returned solution has 56.6% Euclidean error against a synthetic known target,
but relative energy error is only `3.64e-7`. The coefficient error is concentrated
in the large component, whose relative residual is almost identical to the
whole graph's relative residual. A component-wise residual check alone would
not resolve the sensitivity within that component.

Tighter stopping materially improves this example:

| Relative backward tolerance | Iterations | Relative error against the known target | Error as a percentage |
|---|---:|---:|---:|
| `1e-8` | 20 | `5.6597e-1` | 56.597% |
| `1e-10` | 36 | `2.6327e-3` | 0.2633% |
| `1e-12` | 49 | `1.7658e-5` | 0.001766% |
| `1e-14` | 63 | `6.9205e-6` | 0.000692% |
| `1e-15` | 69 | `6.9338e-6` | 0.000693% |
| `1e-16` | 100 | `1.4000e-5` | 0.001400% |

These are results for one fixed input with the existing 25-iteration residual
recompute interval. They are not general accuracy guarantees. The `1e-8`
solution stops before its first scheduled restart, and changing the interval
to 100 or 1,000 has little effect on the tolerance sweep. The investigation
provides no basis for changing the restart default.

For applications requiring more precise coefficients, an explicit tighter
setting can be evaluated using the existing API:

```rust
let options = cmg::PcgOptions {
    relative_tolerance: 1e-12,
    ..cmg::PcgOptions::default()
};
```

Validate the setting against known solutions or independent references where
available, and measure the quantities the application actually uses. A sweep
of tolerances can reveal sensitivity and the cost of greater accuracy. Agreement
between successive solves is useful evidence but does not establish a rigorous
forward-error bound. No tolerance or solver policy is changed by this study.

Very tight tolerances encounter arithmetic and input sensitivity. The benchmark
forms `b` by applying the graph to a known floating-point target. An independent
80-digit calculation shows that the exact solution of the rounded, orthogonally
projected input already differs from that target by about 6.27 parts per million.
For the solver's compatibility-projected RHS, the difference is about 5.37 parts
per million. Forming and solving the RHS at high precision recovers the target
to better than `1e-60`. Input formation and projection therefore matter when
interpreting tiny errors. The non-monotone last rows above also show that simply
tightening the stopping criterion toward machine precision can cost iterations
without improving error against the target.

The diagnostic `accuracy` binary reports the original-system residual, known
target error, energy error, component residuals and full-vector fingerprints.
Its optional weighted-forest reference uses subtree currents and integrated
potentials, independently of CMG and PCG. It retains both terms of compensated
subtree sums across parents; a regression test covers cancellation that would
otherwise discard those terms. The corrected reference agrees with the
80-digit solution of the compatibility-projected problem to about `5e-14`
relative error. Its mean-only reference implicitly balances any remaining
compatibility defect at the tree root; it should not be confused with an exact
orthogonal projection of the rounded input.

The optional `--export PATH` mode writes the synthetic graph, target, original
RHS, mean-centered RHS, compatibility-projected RHS and forest references to a
new JSON file for independent analysis. It does not overwrite existing files.
Diagnostic timings and hosted-CI timings are separate from performance
qualification. See the [follow-up report](component-default-followup.md) for
source identities, validation, performance findings and retained evidence.
