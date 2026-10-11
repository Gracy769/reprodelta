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
