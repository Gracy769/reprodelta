# Cloudflare entrypoint

`reprodelta-cloudflare` is a new Worker at
`https://reprodelta-cloudflare.graceperez769.workers.dev`. It fronts the
existing Railway FastAPI service. Cloudflare's free Workers plan cannot run
the Python API, pinned NumPy containers, or Oracle Docker runner. The Worker
does not execute repository code and has no source-host or database secrets.

The separate Firebase project is `reprodelta-cloudflare-20261027`. Its web
configuration is public and is mirrored in `wrangler.jsonc`. Google and
email/password providers are enabled only in this new project. The original
Firebase project's users and settings are unchanged.

The Railway API needs `EDGE_URL` and the four `FIREBASE_EDGE_*` variables
to verify Cloudflare-origin Firebase tokens. The Worker passes its host in
`X-ReproDelta-Edge-Host` so the API renders the matching public config.
The existing `FIREBASE_*` variables keep serving the Railway origin.

GitHub and GitLab OAuth still begin on Railway because their registered
callbacks are there. These sign-ins finish on Railway, so a session created
there is not a Cloudflare-domain session. Repository connections initiated
from a Cloudflare Firebase session also require a future callback migration.
The Worker makes this explicit by redirecting OAuth starts to Railway.

Oracle VM connection details remain deployment variables. Do not enable
`ORACLE_PROJECT_RUNS_ENABLED` or `ORACLE_PROFILE_VALIDATION_ENABLED` until
the VM and isolation tests pass. See `../oracle/.env.example` and
`../../docs/oracle-vm-deployment.md`.

Deploy from this directory with `npm ci && npm run check && npm run deploy`.
Only the Worker named in `wrangler.jsonc` is changed. The separate Firebase
provider configuration can be reapplied with
`firebase deploy --only auth --project reprodelta-cloudflare-20261027`.
