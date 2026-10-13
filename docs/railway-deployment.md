# Railway deployment

The production project is `reprodelta` in the `Gamer Boy's Projects` workspace.
The service `reprodelta-web` is connected to `Gracy769/reprodelta` on `main`; its
public URL is <https://reprodelta-web-production.up.railway.app>. Railway builds
the root `Dockerfile` and uses `/health` as its deployment health check.

Set these variables on `reprodelta-web`:

| Variable | Value |
| --- | --- |
| `APP_URL` | `https://reprodelta-web-production.up.railway.app` |
| `DATABASE_URL` | `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` |
| `INVESTIGATION_STORE` | `postgres` |
| `SECRET_KEY` | Unique random secret of at least 32 characters; never commit it |
| `GITHUB_OAUTH_CLIENT_ID` | Client ID from the Gracy769 GitHub OAuth App |
| `GITHUB_OAUTH_CLIENT_SECRET` | Client secret from that app |
| `GITLAB_OAUTH_CLIENT_ID` and `GITLAB_OAUTH_CLIENT_SECRET` | Optional GitLab application credentials |
| `NEON_AUTH_BASE_URL` and `NEON_AUTH_JWKS_URL` | Optional managed email/Google auth service, only after this public domain is trusted there |
| `NEBIUS_API_KEY` | Optional; without it model suggestions are labeled mock mode |
| `NEBIUS_MODEL_ID` | Optional; defaults to `nvidia/Nemotron-3_5-Lightning` |
| `NEBIUS_CALL_COST_CEILING_USD` | Required for live calls; set a positive per-call reservation after verifying current model pricing. The default `0` blocks billable calls. |

Guest runs of the curated demo always use labeled mock model proposals, even when a
Nebius key is configured. Authenticated investigations use the live provider only
after a positive cost reservation has been configured. This keeps the public demo
usable without allowing anonymous traffic to spend the key.

The GitHub OAuth App callback must be
`https://reprodelta-web-production.up.railway.app/oauth/github/callback`.
For GitLab, use
`https://reprodelta-web-production.up.railway.app/oauth/gitlab/callback`.
Private GitHub repositories require OAuth App `repo` scope, which GitHub grants
as read/write permission even though ReproDelta only calls read endpoints.
Users can instead connect a fine-grained read token for selected repositories.

The workspace SSH key is only for terminal access to running services. Deploys
are authenticated by Railway CLI login or the GitHub integration. The private
SSH key stays on the developer machine and is never stored in this repository.
This release was pushed to GitHub and uploaded with `railway up --service
reprodelta-web --ci`. Automatic redeployment from GitHub commits has not yet
been confirmed in this Railway workspace.

Before considering a deployment complete, check the Railway deployment status,
`/health`, the home page, `/demo/investigation`, and the GitHub sign-in callback.
The latter requires the OAuth client variables and updated callback above.

## Operator verification after merging the hardening PR

1. Deploy the reviewed main revision. Use one API process/replica and a persistent Postgres database. Configure stable `SECRET_KEY`, `APP_URL`, and the provider callback/domain settings. Check `/health` and `/ready`; readiness must establish both pinned versions and the source hashes.
2. Confirm sign-in with your real GitHub/Firebase account. Open a second account or private browser session and confirm it cannot read the first account's investigation, events, exports or repair results. Guest ownership is session-local; it is not a replacement for provider sign-in.
3. Configure Nebius only when ready for a billable test: verify the available model ID and pricing, add the key as the `NEBIUS_API_KEY` variable on `reprodelta-web`, and set `NEBIUS_CALL_COST_CEILING_USD` to a conservative verified bound. Sign in and run one budgeted investigation; confirm model proposals are marked live, provider usage is recorded, failures remain explicit and the actual proposed diff passes or fails based on executions. Do not call a mock proposal live evidence.
4. Download report JSON/text and record the deployed revision. Restart the real service; check that the same owner's completed export is unchanged and a deliberately interrupted job is never reported as completed. Take a database backup before recovery testing. Confirm health after restart and automatic deployment behavior.

On 14 October 2026, the merged PR #8 revision was deployed through the Railway CLI. The live `/health` and `/ready` endpoints passed; readiness verified Postgres, the persistent session key, both pinned NumPy workers, and their project hashes. GitHub and GitLab OAuth start routes redirected to the expected hosts, but a complete third-party sign-in was not tested. A live guest investigation performed a paired run and another session could not read its record. The Nebius key was absent, so no billable model call was made. Disposable CI exercises completed-report persistence and interrupted-work recovery across restarts; those checks are not yet independently verified on the live service. Legacy shared-owner investigations must be handled administratively; their ownership cannot safely be inferred.
