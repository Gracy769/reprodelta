# Oracle Always Free VM deployment

This deployment bundle runs the ReproDelta API, a local PostgreSQL 16 database,
two **curated** pinned NumPy workers, and Caddy HTTPS on one Oracle VM. The
approved-project E2B queue worker is a separate, opt-in Compose profile. Docker
runs on the VM; the developer's computer does not need Docker.

The bundle is ready for an operator-owned VM. It has **not** been deployed or
tested on an Oracle VM. The existing Railway deployment remains in place until
data migration and live checks are complete. Oracle's Always Free capacity is
limited and can be unavailable in a home region.

## Optional local project container runner

The Oracle Docker runner is a separate plugin selected with
`PROJECT_SANDBOX_PROVIDER=oracle_docker`. It uses the same immutable profiles,
Postgres queue, contract checks, and witness reporting as the E2B runner. It
does **not** require an E2B key. It remains disabled by default. Docker
containers share the VM kernel, so this backend is limited to operator-reviewed
repositories; it is not a public arbitrary-code service.
[Docker documents](https://docs.docker.com/engine/security/) the container security boundary,
and [Docker warns](https://docs.docker.com/engine/install/linux-postinstall/)
that Docker socket access gives root-level host privileges. The worker holding
that socket must be treated as a host administrator, kept private, and never
exposed through a public port. The API and source containers receive no socket.

Once the VM exists, build the two reviewed images with
`sh deploy/oracle/build_project_images.sh`. Record the printed `sha256:` image
IDs in `ORACLE_OLD_IMAGE_ID` and `ORACLE_NEW_IMAGE_ID`. These IDs, rather than
mutable tags, are verified before every source transfer. In an approved
profile use template IDs `oracle-numpy-1264` and `oracle-numpy-202` with the
fixed lock digests from [the pilot template proof](pilot-e2b-template-proof.md).
The image build installs packages before any repository source is transferred.
Each runtime container uses `--network none`, a read-only root filesystem,
an in-memory workspace, a non-root adapter process, dropped capabilities,
process/memory/CPU limits, and a 15-minute maximum. The worker removes the
container after success or failure. The actual host isolation and cleanup
must be tested on the VM; the Windows development host has no Docker.
At startup, the single Oracle worker removes containers bearing its project
sandbox label that a previous worker process left behind. Do not run multiple
Oracle project workers against the same Docker daemon.

Set `DOCKER_GID` to `stat -c %g /var/run/docker.sock` on the VM so the
non-root worker can use the socket. Set `PROJECT_SANDBOX_PROVIDER=oracle_docker`
on the API through `.env`; keep `ORACLE_PROJECT_RUNS_ENABLED=false` and
`ORACLE_PROFILE_VALIDATION_ENABLED=false` until isolation, malicious-source,
cost allocation, restart, and cleanup tests pass. The `ORACLE_SANDBOX_USD_PER_SECOND`
and `ORACLE_PROJECT_RUN_COST_CEILING_USD` settings are an operator-defined
resource accounting limit, not an Oracle charge quote; leave both at zero
until a defensible allocation is chosen. Start the worker only after these
values and image IDs are configured:

Before enabling the flags, run the synthetic isolation probe from the VM. It
checks both image locks and NumPy versions, executes a fixed numeric input,
checks that service secrets and an outbound HTTPS response are absent, then
checks container removal after normal completion, adapter failure, and timeout:

```sh
docker compose --env-file deploy/oracle/.env -f deploy/oracle/compose.yaml \
  --profile oracle-projects run --build --rm project-worker-oracle \
  python -m app.providers.oracle_probe
```

This is a necessary smoke test, not proof against kernel escape or every
network destination. Record the output and image IDs in a private release log.
Then review an approved public repository, a private repository with explicit
consent, concurrent queue claims, restart recovery, and cancellation before
turning on either flag. The VM and Docker daemon do not exist in this checkout,
so these checks remain pending.

```sh
docker compose --env-file deploy/oracle/.env -f deploy/oracle/compose.yaml \
  --profile oracle-projects up --build -d project-worker-oracle
```

Only one project backend should be enabled at a time. Queue claims and worker
presence are provider-specific, so an E2B worker cannot consume an Oracle job.
Existing E2B approvals use different template IDs and need a new approved
profile version and new location-specific owner consent for Oracle; historical runs remain readable. Browser consent
and the run plan identify which execution location is selected.

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
  The API checks the worker's recent Postgres heartbeat before accepting new
  project jobs; `/ready` reports it only after an E2B feature flag is enabled.

The curated workers receive no provider or database credentials, have no
public ports, and use an internal Docker network. The E2B worker receives
database, snapshot-encryption, and E2B credentials only when enabled. It purges
expired encrypted source snapshots on startup and then hourly; if it is stopped,
expired rows remain until it starts again. Repository code is never
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
