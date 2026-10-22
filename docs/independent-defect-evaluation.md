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
profile has not been approved or executed by the E2B worker, so no
end-to-end finding is claimed. A full-isce3 result would require importing
and running the actual upstream project and checking its output product.
