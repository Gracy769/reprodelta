# Sign-in provider setup

ReproDelta uses the managed Neon Auth instance provisioned for its Antideploy
application. OAuth sign-in is separate from granting repository access: a
GitHub identity alone does not supply ReproDelta's limited repository token.
Users connect GitHub or GitLab in the workspace after signing in.

## GitHub sign-in

1. Obtain admin access to **the existing Neon Auth project and branch** behind
   ReproDelta's `NEON_AUTH_BASE_URL`. Do not create an unrelated Neon project:
   users and sessions would be stored elsewhere. Antideploy currently lists
   managed social login as **planned** and does not offer an OAuth-provider
   settings API for its provisioned Auth instance. Until that changes or the
   matching Neon project is made manageable, creating a GitHub OAuth App does
   **not** activate the ReproDelta GitHub sign-in button. Do not place its
   Client Secret in ReproDelta's app secrets expecting that to enable it.
2. In GitHub, open **Settings → Developer settings → OAuth Apps → New OAuth App**.
   Name it ReproDelta. Use `https://reprodelta-lab.antideploy.app` for the homepage.
   Use the exact callback `NEON_AUTH_BASE_URL + /callback/github`, including the
   `/neondb/auth` path. For the current instance this is
   `https://ep-square-waterfall-b3uff38x.neonauth.c-4.ap-southeast-1.aws.neon.tech/neondb/auth/callback/github`.
   Check the instance URL in Antideploy before saving in case it changed.
3. Copy the GitHub Client ID and generate a Client Secret. In the **matching**
   Neon Console branch, open Auth → OAuth providers, enable GitHub, and save
   those credentials. Keep the secret in Neon only, never in the website or
   Git repository. Neon's CLI also documents
   `neon neon-auth oauth-provider add --provider-id github --oauth-client-id ... --oauth-client-secret ...`
   after selecting the correct project and branch.
4. Ensure `https://reprodelta-lab.antideploy.app` is a trusted Auth domain. Test
   GitHub sign-in in a fresh browser session, then test returning to the
   workspace after reload. GitHub sign-in does **not** automatically connect
   private repositories; use the workspace's limited read token flow.

## GitLab

Managed Neon Auth's provider-management documentation lists Google, GitHub,
and Vercel; it does not expose GitLab as a configurable provider. The prior
GitLab social button reported `PROVIDER_NOT_SUPPORTED`, so it has been removed.
To review GitLab repositories now, sign in with email, Google, or an activated
GitHub account; then connect GitLab in the workspace with a `read_api` token.
GitLab's **Profile → Access → Applications** OAuth app flow is relevant only
if the project later adopts an Auth service that supports GitLab sign-in. Do
not create a GitLab OAuth app for the current managed Auth instance.

## Google return flow

Neon redirects with a one-time `neon_auth_session_verifier`. ReproDelta starts
social sign-in through its first-party `/auth/sign-in/social` bridge, preserving
the matching challenge cookie. The root and `/workspace` routes exchange the
verifier with Neon `/get-session`, set the session cookie on ReproDelta's
origin, and redirect to `/workspace` without keeping the verifier in the URL.
If the challenge is missing or exchange fails, sign-in returns to `/signin`
with a retry message. Validate the full Google round trip on the deployed site
after the next release; local tests simulate the provider response.

Sources: [Neon OAuth setup](https://neon.com/docs/auth/guides/setup-oauth),
[Neon Auth CLI provider list](https://neon.com/docs/cli/neon-auth),
[GitHub OAuth application setup](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app),
[GitLab OAuth applications](https://docs.gitlab.com/integration/oauth_provider/).
