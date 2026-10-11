# Sign-in provider setup

ReproDelta's source-host sign-in runs in the application. It does not depend on
Antideploy or Railway account OAuth. The GitHub and GitLab client credentials
live in Railway service variables, and provider tokens are encrypted in Postgres.
The sign-in callback creates an HttpOnly, Secure session cookie. The OAuth state
challenge expires after ten minutes.

## GitHub

1. In the Gracy769 GitHub account, open **Settings → Developer settings → OAuth
   Apps → ReproDelta**.
2. Set the homepage to
   `https://reprodelta-web-production.up.railway.app` and callback to
   `https://reprodelta-web-production.up.railway.app/oauth/github/callback`.
3. Put its Client ID and Client Secret in Railway's `reprodelta-web` variables as
   `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET`. Do not paste the
   secret into chat or commit it.
4. Test sign-in, reload `/investigations/new`, and confirm the account's
   repositories appear.

GitHub OAuth Apps have no private-repository read-only scope. To list private
repositories, the app requests `repo`, which gives broader read/write consent
on GitHub even though ReproDelta only uses read endpoints. Users who prefer
limited access can sign in by another method and connect a fine-grained GitHub
read token for selected repositories in the workspace.

## GitLab

Create a GitLab OAuth application with callback
`https://reprodelta-web-production.up.railway.app/oauth/gitlab/callback` and
`read_api` scope. Set `GITLAB_OAUTH_CLIENT_ID` and
`GITLAB_OAUTH_CLIENT_SECRET` on `reprodelta-web`. Until those variables are
configured, GitLab sign-in returns a clear unavailable message; a signed-in
user may still connect GitLab with a `read_api` personal token.

## Google and email

The earlier managed Neon Auth bridge is still present for Google and email.
Those methods need a Neon Auth instance with the Railway domain trusted, plus
`NEON_AUTH_BASE_URL` and `NEON_AUTH_JWKS_URL`. Railway's own GitHub account
integration does not configure end-user Google or email auth. Do not claim those
methods work on Railway until their full redirect and session flow is tested.

Sources: [GitHub OAuth web flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps),
[GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps),
[GitLab OAuth applications](https://docs.gitlab.com/integration/oauth_provider/).
