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
**only `read_api` scope**. `read_repository` alone does not grant the project,
merge request, and issue API access that the picker needs. Runner, write,
registry, observability, profile, and email scopes are unnecessary. Set
`GITLAB_OAUTH_CLIENT_ID` and
`GITLAB_OAUTH_CLIENT_SECRET` on `reprodelta-web`. Until those variables are
configured, GitLab sign-in returns a clear unavailable message; a signed-in
user may still connect GitLab with a `read_api` personal token. The app encrypts
GitLab refresh tokens and rotates them when access tokens expire.

## Google and email with Firebase

1. Create a Firebase project on the no-cost Spark plan and register a Web app.
   Spark does not require a payment method for Google Authentication.
2. In **Authentication → Sign-in method**, enable Google and Email/Password. Add
   `reprodelta-web-production.up.railway.app` to **Authentication → Settings →
   Authorized domains**.
3. In Railway's `reprodelta-web` service, set the Web app's public config fields
   as `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, and
   `FIREBASE_APP_ID`. The API key is a public browser identifier, not an admin
   credential. Railway redeploys the app after variable changes.
4. Test **Continue with Google** on `/signin`. The app uses a popup. Test
   email sign-up by verifying the address from the email Firebase sends, then
   signing in. The app verifies either Firebase ID token server side and stores
   a Secure, HttpOnly ReproDelta session cookie. Then connect GitHub on
   `/workspace` to show repositories without replacing that session.

The older managed Neon Auth bridge remains as a fallback only when Firebase
Web app variables are absent and `NEON_AUTH_BASE_URL` is configured.

Sources: [GitHub OAuth web flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps),
[GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps),
[GitLab OAuth applications](https://docs.gitlab.com/integration/oauth_provider/).
Firebase: [Spark pricing](https://firebase.google.com/pricing),
[Google sign-in](https://firebase.google.com/docs/auth/web/google-signin),
[ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens).
