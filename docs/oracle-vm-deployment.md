# Oracle Always Free VM deployment

This deployment bundle runs the ReproDelta API, a local PostgreSQL 16 database,
two **curated** pinned NumPy workers, and Caddy HTTPS on one Oracle VM. The
approved-project E2B queue worker is a separate, opt-in Compose profile. Docker
runs on the VM; the developer's computer does not need Docker.

The bundle is ready for an operator-owned VM. It has **not** been deployed or
tested on an Oracle VM. The existing Railway deployment remains in place until
data migration and live checks are complete. Oracle's Always Free capacity is
limited and can be unavailable in a home region.

## Prepare the VM

1. Create an Oracle Ampere A1 (Arm64) VM with an Always Free-eligible shape and
   Ubuntu, a public IP, and SSH access. Reserve enough free storage for images,
   PostgreSQL, backups, and build cache. The base Python and Postgres images
   support Arm64; the actual multi-image build remains to be verified on the VM.
2. In the Oracle network security group or security list, allow inbound TCP 80
   and 443 from the internet, and SSH 22 only from your own IP. Do not expose
   5432, 8000, or 9000. Also check the Ubuntu host firewall. See
   [Oracle security rules](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm).
3. Install [Docker Engine and the Compose plugin for Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
   **on the VM**, then verify `docker compose version`. Use `sudo` or a trusted
   deployment user with Docker permissions; Docker group membership grants
   host-level privileges.
4. Point a DNS A record for the chosen domain to the VM public IP. Caddy obtains
   HTTPS certificates when the hostname resolves and ports 80/443 are reachable.
   See [Caddy automatic HTTPS](https://caddyserver.com/docs/quick-starts/https).

## Configure and start

On the VM, clone the reviewed `main` revision and run:

```sh
cd reprodelta
python3 deploy/oracle/init_env.py
${EDITOR:-vi} deploy/oracle/.env
chmod 600 deploy/oracle/.env
sh deploy/oracle/deploy.sh
```

`init_env.py` generates a random database password, persistent session secret,
and 32-byte base64 snapshot key. It refuses to overwrite an existing `.env`.
Set `PUBLIC_DOMAIN` to your real hostname. Keep `.env` off Git; it is ignored.
The `DATABASE_URL` password must match `POSTGRES_PASSWORD`. The initializer sets
both to the same URL-safe hex value. If restoring an existing database, retain
its matching password and the previous `SECRET_KEY` if existing encrypted
source connections and sessions must remain readable.

Provider configuration must match the new origin:

- `APP_URL` and `AUTH_ALLOWED_ORIGIN` are set by Compose from `PUBLIC_DOMAIN`.
- Set the GitHub OAuth callback to `https://DOMAIN/oauth/github/callback`, then
  fill `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET`.
- For GitLab, use `https://DOMAIN/oauth/gitlab/callback` and its client values.
- For Firebase, add `DOMAIN` to authorized domains and fill the four Firebase
  values; Google sign-in needs a real browser check after migration.
- Keep `NEBIUS_CALL_COST_CEILING_USD=0` until a current cost bound is verified.
- Keep both E2B execution flags `false` until the approved-project security,
  private-project, cost, and restart gates in
  [approved-project-pilot.md](approved-project-pilot.md) pass. Do not start the
  `approved-projects` Compose profile before then. Once gated and configured,
  start it with `docker compose --env-file deploy/oracle/.env -f
  deploy/oracle/compose.yaml --profile approved-projects up -d project-worker`.

The curated workers receive no provider or database credentials, have no
public ports, and use an internal Docker network. The E2B worker receives
database and E2B credentials only when enabled. Repository code is never
executed in the API container; approved project execution belongs in E2B.

## Cutover and verification

The new local PostgreSQL volume starts empty. Export and restore the current
Railway database **before** pointing users at the Oracle domain if you need
existing accounts, connections, approvals, and investigations. Preserve the
same `SECRET_KEY` to decrypt stored source tokens; rotating it requires users to
reconnect their sources. Make a separate, encrypted off-VM backup and verify
restore. Do not commit dumps, `.env`, API keys, or OAuth secrets.

After deployment, check `docker compose --env-file deploy/oracle/.env -f
deploy/oracle/compose.yaml ps`, `https://DOMAIN/health`, and `https://DOMAIN/ready`.
Then verify home, sign-in and both OAuth callbacks, a curated paired run, report
export, and recovery after an API container restart. `/ready` checks database,
secret, and pinned workers; it does not exercise provider authentication or live
Nebius/E2B calls. Keep Railway available until the new domain passes these
checks. A DNS or auth callback change is a separate cutover step, not part of
running the Compose bundle.

Use `sh deploy/oracle/deploy.sh` for later builds from the reviewed checkout.
Back up Postgres and the artifact volume before upgrades. Never run
`docker compose down -v` on production: `-v` deletes the database and artifact
volumes. The service is one VM and one API replica; it has no high-availability
failover. Monitor free disk and memory, especially during image builds.
