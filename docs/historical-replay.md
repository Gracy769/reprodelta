# Independent least-squares compatibility case

NumPy 2.0 intentionally changed the default `numpy.linalg.lstsq` cutoff from
machine precision to machine precision times `max(M, N)`. This independently
documented mechanism differs from NEP-50 scalar promotion. Upstream change:
[NumPy PR 25721](https://github.com/numpy/numpy/pull/25721), merged commit
[`80acab201eb5445af925552468983073616284dc`](https://github.com/numpy/numpy/commit/80acab201eb5445af925552468983073616284dc).
See the [release notes](https://numpy.org/doc/2.0/release/2.0.0-notes.html).

Our replay uses a bounded 4x2 float64 matrix, fixed observations `[1,1,0,0]`, and
the declared outcome `rank/2 > 0.75`. The near-singular diagonal example can
change the reported rank from two to one, changing that outcome. The matrix is
constructed here; it is not copied from an independently observed scientific
workflow. Neither outcome is called a scientific bug. Choosing `rcond` explicitly
is a possible compatibility intervention, not a mathematically approved repair.
Constrained model repair remains limited to the original power-ratio pipeline.

The benchmark now includes power ratio, sensor offset, least-squares rank and
an unchanged control. Each receives fixed replay, seeded random valid search and
an explicitly informed boundary sweep at equal paired-execution budgets. Raw
inputs, actual installed NumPy versions, outputs, contract and source hashes,
seed and first-witness position are exported. Informed search knows the boundary;
the comparison cannot establish general search superiority. The ordinary
investigation API also accepts `search_strategy` (`sweep` or `random`) and
`search_seed`; random mode no longer silently runs the known-boundary sweep.

Export/replay commands:

```sh
python scripts/export_demonstration.py --old-python OLD --new-python NEW --pairs 4 --output evidence.json
python scripts/export_demonstration.py --old-python OLD --new-python NEW --replay evidence.json --output replay.json
```

Remaining research validation needs an independently sourced downstream defect,
several held-out cases chosen before search, multiple seeds, false-alarm counts
and an independent domain review. Those conclusions cannot be manufactured by
adding another fixture or by passing CI.

The committed [comparison evidence](evidence/defaults-comparison.json) contains
96 native interpreter runs. [Independent replay](evidence/defaults-replay.json)
matched all 96 exact outputs and runtime versions. SHA-256 sidecars accompany
both exports. These files contain only curated inputs and trusted source.
