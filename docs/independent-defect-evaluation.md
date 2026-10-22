# Independent NumPy defect candidate

The [isce3 issue #335](https://github.com/isce-framework/isce3/issues/335)
reports that its InSAR mask calculation left shifts `np.uint8` scalars by 16
and 8 bits. With NumPy 2, the shifted values can become zero and silently drop
exception-mask bits. The issue includes a public minimal reproduction and points
to the affected project source. NumPy's [2.0 migration guide](https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html#changes-to-numpy-data-type-promotion)
documents the scalar promotion change. This is an independently reported
defect, separate from ReproDelta's constructed signal classifier.

`evaluation/isce3_mask_shift_repro.py` isolates only the reported expression.
It includes a nonzero input, a zero-mask unchanged control, two identical
executions per input, and a source-level `int()` cast as the proposed repair.
It does **not** import or run isce3, validate the full InSAR product, establish
the operational impact, or prove that ReproDelta's general project onboarding
would have detected it. The reproduction is an evaluation case, not an
approved-project finding.

To replay on Python 3.12 without Docker:

```powershell
uv run --no-project --python 3.12 --with numpy==1.26.4 python evaluation/isce3_mask_shift_repro.py
uv run --no-project --python 3.12 --with numpy==2.0.2 python evaluation/isce3_mask_shift_repro.py
```

Observed on Windows with Python 3.12.13 (two identical executions per cell):

| Input | NumPy 1.26.4 | NumPy 2.0.2 | Cast repair on both |
| --- | ---: | ---: | ---: |
| Reference 3, secondary 1 | `196869` (`0x30105`) | `5` (`0x5`) | `196869` (`0x30105`) |
| Reference 0, secondary 0 | `5` (`0x5`) | `5` (`0x5`) | `5` (`0x5`) |

The command outputs report the exact NumPy versions and both repeat values.
Record platform-specific differences before counting this case in a benchmark.
Fixed samples, random search, and ReproDelta must receive equal
execution budgets. A discovery claim requires running the source through the
approved-project pipeline with a predeclared contract; this script alone is
insufficient.

## Public project for the approved-project pipeline

The independent [evaluation repository](https://github.com/Gracy769/reprodelta-isce3-mask-evaluation)
contains only the expression from the reported issue and a bounded adapter.
Its [open NumPy upgrade PR](https://github.com/Gracy769/reprodelta-isce3-mask-evaluation/pull/1)
changes `requirements.txt` from 1.26.4 to 2.0.2 and stays open as a
source-host test change. The base and head commits are
`34e1dcd88dc2def1fd8e736313817f070c003a36` and
`5c4c09cdef28716485d78ad1fe4db564d935506f`. The ready-to-review
[profile draft](../evaluation/isce3-mask-project-profile.json) declares two
integer mask IDs, the packed-mask measurement, and a decision at 65535.

ReproDelta's source analyzer read the live GitHub PR at those commits,
identified only the exact NumPy pin change, admitted it under the pilot
eligibility rule, and verified the base source tree by blob hash. That
snapshot's SHA-256 was
`4a5ef877826342c5d5d5ae3d75ca003b087b9cae6fec5461178c93ef1f2b3705`.
These checks establish source identity and admission, not an E2B result.

Local execution of that adapter gave `196869` on NumPy 1.26.4 and `5` on
2.0.2 for the declared sample; a zero/zero control returned `5` in both.
Those commands ran the harness directly on Windows, outside ReproDelta. The
same base source was then executed directly through ReproDelta's E2B batch
adapter in one prepared, offline sandbox per NumPy version. The two repeated
sample outputs were `196869/196869` on 1.26.4 and `5/5` on 2.0.2; the
zero-mask control returned `5` in both. The source digest, input hashes and
template lock digests were checked, and reconnecting to each sandbox after
cleanup returned `SandboxNotFoundException`. The bounded run used 17.875
seconds of sandbox time, an estimated `$0.00082225` at E2B's published default
[compute and memory rates](https://e2b.dev/pricing); the two 120-second
timeouts capped the reserved estimate at `$0.01104`. See the
[observation record](evidence/independent-mask-e2b-2026-10-22.json).

This establishes a real sandbox-level reproduction of the narrow expression.

## Local queued-project proof

The public profile then completed ReproDelta's real backend sequence against
an isolated local PostgreSQL 18 database: create the draft, lease a validation
job, execute its sample in both E2B templates, approve against that completed
validation record, lease the PR investigation, and save a bounded finding.
The investigation ran its sample twice in each environment and recorded
`196869/196869` versus `5/5`, a repeatable changed conclusion. The two jobs
used 38.032 observed sandbox seconds and recorded `$0.001750` in estimated
sandbox usage under separate `$0.01` per-job ceilings. All four sandbox IDs
were not reconnectable after cleanup. See the
[queued-run evidence](evidence/independent-mask-queued-2026-10-22.json).

This was a local operator simulation with a temporary database, not an
approval by an independent maintainer or a Railway worker deployment. Browser
workflow, live sign-in, restart recovery, a private project, and the second
approved project remain unverified. A full-isce3 result would require
importing and running the actual upstream project and checking its output
product.
