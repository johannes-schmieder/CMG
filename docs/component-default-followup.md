# Component CMG follow-up — September 7, 2026

The two focused issues are now better resolved. The weighted-path discrepancy
comes from the existing backward-residual stopping contract and ill-conditioning;
tighter tolerances substantially reduce it. The remaining connected performance
control passes an independent confirmation of the frozen 2% allowance, while
showing a small measurable slowdown. Production numerical code and defaults
remain unchanged by this follow-up.

The work is separate from the completed [default study](component-default-study.md).
The original 21-round result and evidence archive remain immutable. This pass
focuses on the accuracy diagnosis and the unresolved control. It does not
complete the omitted multi-hour large-case matrix or a controlled x86 performance
qualification, and it does not promote B on main or run an SCC campaign.

The first accuracy screen froze 28 calls at source
`2dde41b1bcc85da37889d7a4f0b50cca95e9ec90`: four tolerances and three restart
intervals on the large weighted mixture, plus smaller weighted and unweighted
controls. Every call converged within the unchanged 1,000-iteration cap.
On the problematic large mixture, relative target error falls from 56.6% at
`1e-8` to 0.001766% at `1e-12`, with iterations increasing from 20 to 49.
The restart sweep offers little benefit. The
[accuracy guide](pcg-accuracy.md) explains the certificate, complete tolerance
table, energy error and practical use of explicit tighter settings.

An independent 50/80-digit weighted-forest calculation separates input
sensitivity from solver truncation. The two precisions agree well beyond the
reported digits. Exact solutions of the rounded original and solver-projected
RHSs differ from the known target by about 6.27 and 5.37 parts per million,
respectively. High-precision RHS formation removes that discrepancy. The
diagnostic reference initially collapsed compensated subtree pairs too early;
the archived initial references are not treated as exact ground truth. The
correction at `4c75ceb19c0ce7230bc24198d0dfbb280ad82174` retains both terms,
adds a cancellation regression test, and agrees with an 80-digit solve of the
solver-projected problem to `4.90e-14` relative error. A mean-only projection
leaves a different tiny compatibility defect; the report keeps that distinction
explicit.

The corrected diagnostic driver was compiled separately against checkpoint
`90d06d58edf7de43e6e78855b1b14c4b6311b808` and B
`1d0396f805fb358106c592c6362506d3cb01517d`. At both `1e-8` and `1e-12`, their
iterations, restarts, certificate fields and returned-vector fingerprints agree.
Both require 49 iterations at `1e-12`. This isolates the accuracy finding from
the component optimizations. Diagnostic solve times on that case were about
2.89 seconds for checkpoint and 0.132 seconds for B at `1e-12`; these one-shot
observations illustrate the cost difference but are not qualified speedup
estimates. No numerical source was tuned to the diagnostic inputs.

The performance confirmation uses the original frozen minimal timing binaries:
checkpoint SHA-256
`9fcd135df5881a7f39584f56e18ea18356c415abede296b1f489e31304defad9`
and B SHA-256
`eb2fd58a4ac9686117cc5bc2786fb68ce6a5d74a2707b0114ab7a4f7e55cd254`.
It measures `large-dense-connected-worker-firm`, seed 20260908, one RHS and the
serial caller-buffer path on the same Apple M2 Ultra. Thirty predeclared blocks
balance ABBA and BAAB order, with 20 recorded repetitions and two warmups per
invocation. Each arm's block time averages its two invocation medians. The
reported statistic is the median of the 30 paired block ratios, with 10,000
bootstrap resamples and fixed seed 20260907. There are no extensions.

| Independent connected control | Result |
|---|---:|
| Recorded solves | 2,400 |
| Checkpoint/B total-time ratio | 0.995330× |
| Exploratory 95% interval | [0.994147, 0.997392] |
| B's implied slowdown | 0.469% |
| Interval for slowdown | approximately [0.262%, 0.589%] |
| Frozen allowable slowdown | 2% |
| Result | Passes this local margin |

The entire interval is below unit speedup, so this supports a small slowdown
rather than exact parity. It also rules out a 2% slowdown under this protocol.
Every recorded numerical fingerprint and diagnostic matches its corresponding
original source/case record. The original inconclusive interval is not pooled
with this result or rewritten. Scheduling remains uncontrolled macOS scheduling
without an exclusive core; one-minute load averages ranged from about 2.56 to
14.09 on the 24-core machine. Agent compilation and other numerical experiments
were stopped during timing. The confidence interval is exploratory and local.

An additional x86 correctness screen ran on a GitHub Ubuntu host reporting
`AMD EPYC 7763 64-Core Processor`, four logical CPUs and four affinity CPUs.
All four predefined cases passed, including the large weighted problem at
`1e-8` and `1e-12`. Their full-vector fingerprints, iteration counts, allowed
residual and backward error match the M2 results. Independently computed norm summaries
can differ in their last digits across platforms. The host is virtualized,
shared infrastructure; its diagnostic timings do not qualify production
performance or substitute for the remaining x86 performance study.

[CI for the diagnostic implementation](https://github.com/johannes-schmieder/CMG/actions/runs/34083695049)
passes quality and Ubuntu/macOS/Windows library tests at
`496be2109b3d2eb4a9999d5cf76e94fdc37654bc`. The quality job runs the diagnostic
reference tests and retains the four-case x86 screen as a checksummed artifact.
The reference has five local tests including shared fixture checks. The final
evidence receipt records final-documentation-head CI separately.

A [machine-readable follow-up summary](../.ci/performance/component-default-followup.json)
records the timing interval, tolerance results, source identities and audit hashes.

Evidence is retained in the new `cmg-accuracy-followup-20260907`,
`cmg-accuracy-reference-20260907`, `cmg-accuracy-reference-fixed-v2-20260907`,
`cmg-connected-confirmation-20260907` and `cmg-accuracy-ci-496be21` directories
under `/private/tmp`. The initial failed lockfile build is also retained under
`cmg-accuracy-reference-fixed-20260907`; its corrected launcher uses a fresh
directory and preserves dependency-feature mappings. The new package is
`/private/tmp/cmg-component-followup-evidence-20260907.tar.gz`, with a separate
checksummed receipt. It includes raw records, plans, source and binary identities,
precision calculations, failures, corrected references and CI artifacts. The
original default-study archive remains unchanged at SHA-256
`db713f760465a88c65577fe05ba2c36c06eec2bfed78e19ffe3014360742c3db`.

The next default-promotion step is to finish the omitted comparisons and obtain
controlled performance evidence on representative x86 hardware. The local
connected control no longer needs repeated attempts. Applications needing more
precise coefficients can evaluate an explicit `1e-12` setting now; a broader
accuracy-policy change should follow application-specific validation in a
separate PR. Further kernel tuning has lower priority than these remaining
qualification and accuracy-contract decisions.
