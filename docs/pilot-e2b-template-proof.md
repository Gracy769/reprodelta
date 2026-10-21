# Fixed pilot E2B environment proof

On 2026-10-21, the operator built two E2B templates using
`scripts/build_e2b_templates.py --build`. The build installed only an exact
NumPy version into a Python 3.12 image and wrote the fixed lock marker. No
repository source or service credential was placed in the templates.

| Environment | Template ID | Lock SHA-256 |
| --- | --- | --- |
| NumPy 1.26.4 | `o0g0xd5k79nqzrgfk8bo` | `6e05463defe47c155b59f05b01fcdb0752ddba2cd58538e330f42e8ddf70b5e5` |
| NumPy 2.0.2 | `h993tf72q67zyzk1u4u8` | `0f977035ee9fa346c26b609f63a6656650dc35b088746df63338cf20574a23e0` |

`scripts/verify_e2b_templates.py` launched each template with internet access
disabled, verified its NumPy version and lock marker before uploading a
synthetic adapter, ran one fixed input, and destroyed the sandbox. Both returned
ratio 0.5. Observed complete sandbox lifetimes were 7.92 and 6.69 seconds.
This proves the fixed template path only; it does not validate a private
project or the complete queue and cancellation behavior.

The [E2B pricing guide](https://e2b.dev/pricing) listed default 2 vCPU at
$0.000028/s and default 4 GiB RAM at $0.000018/s when checked on 2026-10-21.
The combined reference rate is **$0.000046 per sandbox second**. An 1,800
second pilot allowance estimates $0.0828; use a $0.10 ceiling to allow for
minor metering differences. Verify the actual account rate and template compute
configuration before setting `E2B_SANDBOX_USD_PER_SECOND` and
`E2B_PROJECT_RUN_COST_CEILING_USD` in Railway. The app leaves them at zero and
rejects starts until configured.

Production `E2B_PROJECT_RUNS_ENABLED` remains false. Before enabling it,
validate one public and one private approved project, isolated Postgres queue
behavior, cancellation/cleanup, malicious source, and a rotated E2B key.
