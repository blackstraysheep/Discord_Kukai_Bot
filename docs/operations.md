# Operations Runbook

## Production Environment

The current production deployment runs on Oracle Cloud Infrastructure (OCI).

- Instance: `kukai-bot`
- Image: Canonical Ubuntu 24.04 Minimal aarch64
- Shape: `VM.Standard.A1.Flex`
- Runtime path: `/home/ubuntu/kukai_bot`
- Runtime user: `ubuntu`
- Containers:
  - `bot`: kukai bot image built from this repository
  - `db`: `postgres:16`
- Database: PostgreSQL inside the Compose network
- PostgreSQL data volume: `kukai_bot_postgres_data`
- Local backup directory on the VM: `/home/ubuntu/kukai_backups`
- VM timezone: `Asia/Tokyo`

The OCI account is upgraded to Pay As You Go, but production should stay inside
the Always Free A1 envelope. Keep these guardrails in place:

- Budget alert for unexpected spend.
- Compute quota policy for A1 resources, currently intended as:
  - `standard-a1-core-count`: 2
  - `standard-a1-memory-count`: 12
- No public PostgreSQL port.
- No load balancer.
- No extra block volumes unless explicitly planned.
- No reserved public IP unless the cost impact has been checked.

Inbound networking should allow SSH only. PostgreSQL must remain reachable only
inside the Docker Compose network.

## Production Access

Connect from the trusted admin machine:

```powershell
ssh -i $HOME\.ssh\id_ed25519 ubuntu@<public-ip>
```

After logging in:

```sh
cd /home/ubuntu/kukai_bot
```

Confirm the host is the expected OCI A1 machine:

```sh
uname -m
lsb_release -a
timedatectl
```

Expected:

- `uname -m`: `aarch64`
- Ubuntu 24.04 LTS
- timezone: `Asia/Tokyo`

## Standard Operation

Before starting the stack, set required secrets in `.env`:

```env
BOT_TOKEN=...
POSTGRES_PASSWORD=...
```

`POSTGRES_PASSWORD` has two roles:

- On the first PostgreSQL container initialization, the official PostgreSQL image uses it to create the database user.
- On every bot startup, Compose uses it to build `DATABASE_URL` and `SCHEDULER_DB_URL`, so the bot can log in to PostgreSQL.

If `postgres_data` already exists, changing `.env` does not change the password stored inside PostgreSQL. In that case, `.env` must match the existing DB password, or you must change the DB user password explicitly:

```powershell
docker compose exec db psql -U kukai -d kukai -c "ALTER USER kukai WITH PASSWORD 'new-strong-password';"
```

Then update `.env`:

```env
POSTGRES_PASSWORD=new-strong-password
```

Start the PostgreSQL-backed stack:

```sh
docker compose up -d
```

Follow bot logs:

```sh
docker compose logs -f bot
```

Stop the stack:

```sh
docker compose down
```

Do not use `down -v` unless intentionally deleting the PostgreSQL database volume.

## Local Test Environment

Use a separate Discord application and bot token for local or staging tests. Do
not run production and test runtimes with the same `BOT_TOKEN` at the same time.

Recommended local setup:

1. Create a test Discord application and bot in the Discord Developer Portal.
2. Invite that test bot to a private test Discord server.
3. Copy `.env.test.example` to `.env.test`.
4. Set `BOT_TOKEN` to the test bot token.
5. Set `DEV_GUILD_IDS` to the test Discord server ID.
6. Keep all PostgreSQL values local/test-only. Do not copy production
   `DATABASE_URL`, `SCHEDULER_DB_URL`, or `POSTGRES_PASSWORD`.

Start the isolated test stack from the repository root:

```powershell
docker compose --env-file .env.test -f docker-compose.test.yml up -d --build
```

Follow the test bot log:

```powershell
docker compose --env-file .env.test -f docker-compose.test.yml logs -f bot
```

Stop the test stack:

```powershell
docker compose --env-file .env.test -f docker-compose.test.yml down
```

The test compose file uses the Compose project name `kukai_bot_test`, so its
network and Docker volumes are separate from the production project. Do not use
`down -v` unless intentionally deleting the local test database.

Expected test environment boundaries:

- Test bot token only.
- Test Discord server only.
- `DEV_GUILD_IDS` set to the test server for fast command sync.
- Local/test PostgreSQL only.
- No production Discord server, production bot token, or production database
  connection values.

### Local test update with Alembic migration

If the branch adds or changes an Alembic migration, do not start only with
`docker compose ... up -d --build`. Build the bot image, run the migration from
inside the Compose network, then start the bot.

From the repository root on Windows:

```powershell
git switch <branch-name>
docker compose --env-file .env.test -f docker-compose.test.yml stop bot
docker compose --env-file .env.test -f docker-compose.test.yml up -d db
docker compose --env-file .env.test -f docker-compose.test.yml build bot
docker compose --env-file .env.test -f docker-compose.test.yml run --rm bot python -m alembic upgrade head
docker compose --env-file .env.test -f docker-compose.test.yml up -d bot
docker compose --env-file .env.test -f docker-compose.test.yml logs --tail 100 bot
```

Use this for feature branches such as `feature/entry-gated-channel` that add a
new column or otherwise require the database schema to move forward.

Do not run host-side `py -m alembic upgrade head` when `DATABASE_URL` points to
`db:5432`. The hostname `db` is a Docker Compose service name and resolves only
inside the Compose network. In that case, run Alembic with
`docker compose ... run --rm bot python -m alembic upgrade head` as shown above.

## Health Checks

Confirm Alembic is at the latest revision:

```sh
docker compose exec bot alembic current
```

Confirm the database is reachable:

```sh
docker compose exec db psql -U kukai -d kukai -c "select count(*) from kukais;"
```

Confirm the select comment schema is fully renamed:

```sh
docker compose exec db psql -U kukai -d kukai -c "\d select_comments"
```

Expected:

- Alembic head is `0014_select_comments_select_id`
- `select_comments` has `select_id`
- `select_comments` does not have `vote_id`

## Restarting

Restart only the bot:

```sh
docker compose restart bot
```

Restart the full stack:

```sh
docker compose down
docker compose up -d
```

Rebuild the bot image when Dockerfile dependencies, TeX packages, or fonts change:

```sh
docker compose build bot
docker compose up -d bot
```

Equivalent one-shot rebuild:

```sh
docker compose up -d --build bot
```

Normal code-only updates should keep Docker's build cache. Do not add
`--no-cache`, run `docker builder prune`, or remove the builder cache unless you
intentionally want to reinstall TeX and rebuild every image layer. The
Dockerfile keeps TeX installation and LuaLaTeX warmup before the application
source copy, so code changes should normally reuse those layers.

Verify VM reboot recovery after infrastructure changes:

```sh
sudo reboot
```

After reconnecting:

```sh
cd /home/ubuntu/kukai_bot
docker compose ps
docker compose logs --tail 50 bot
```

Expected:

- `bot` is `Up`
- `db` is `Up` and healthy
- bot log includes `Context impl PostgresqlImpl.`
- bot log includes `Logged in as ...`

## Production Updates

The current manual deployment method is to copy the repository from the admin
machine to the VM and rebuild the Compose stack there. Do not copy local runtime
data, test caches, or local secret files.

From the local repository root on Windows:

```powershell
tar --exclude='.git' --exclude='data' --exclude='.pytest_cache' --exclude='.env' -czf C:\tmp\kukai_bot.tar.gz .
scp -i $HOME\.ssh\id_ed25519 C:\tmp\kukai_bot.tar.gz ubuntu@<public-ip>:/home/ubuntu/
```

On the VM:

```sh
cd /home/ubuntu
cp -a kukai_bot kukai_bot.before_update_$(date +%F_%H%M)
tar -xzf kukai_bot.tar.gz -C kukai_bot
cd /home/ubuntu/kukai_bot
docker compose up -d --build bot
docker compose logs --tail 100 bot
```

If the update includes Alembic migrations, stop the bot before rebuilding,
keep PostgreSQL running, run the migration from inside the Compose network, and
then start the bot:

```sh
cd /home/ubuntu/kukai_bot
docker compose stop bot
docker compose up -d db
docker compose build bot
docker compose run --rm bot python -m alembic upgrade head
docker compose up -d bot
docker compose logs --tail 100 bot
```

Plain `docker compose up -d --build bot` is not enough for migration-bearing
updates because Compose does not run Alembic automatically.

Before updating production:

1. Run the relevant test set locally.
2. Confirm `.env` on the VM still contains production values.
3. Take a manual PostgreSQL dump.
4. Deploy and rebuild.
5. Confirm Discord login and command sync in the bot log.

Expected post-deploy checks:

```sh
cd /home/ubuntu/kukai_bot
docker compose ps
docker compose logs --tail 100 bot
```

Expected:

- `bot` is `Up`.
- `db` is `Up` and healthy.
- bot log includes `Context impl PostgresqlImpl.`
- bot log includes `Registered persistent kukai views: <count>`.
- bot log includes `Logged in as ...`.

Manual pre-update dump on the VM:

```sh
cd /home/ubuntu/kukai_bot
docker compose exec -T db pg_dump -U kukai -d kukai > /home/ubuntu/kukai_backups/pre_update_$(date +%F_%H%M).sql
```

If a code update fails before any database migration changes have been used,
roll back the files from the timestamped directory and rebuild:

```sh
cd /home/ubuntu
rm -rf kukai_bot
cp -a kukai_bot.before_update_<timestamp> kukai_bot
cd /home/ubuntu/kukai_bot
docker compose up -d --build
```

If a migration has already modified production data, treat rollback as a database
restore task and do not rely only on file rollback.

If the bot restart-loops with a PostgreSQL password error such as
`asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "kukai"`,
the most likely cause is that the VM's production `.env` was overwritten. Restore
`.env` from the timestamped backup directory and restart only the bot:

```sh
cd /home/ubuntu
ls -td kukai_bot.before_update_* | head -1
cp /home/ubuntu/<backup-dir>/.env /home/ubuntu/kukai_bot/.env
cd /home/ubuntu/kukai_bot
docker compose up -d bot
docker compose logs --tail 100 bot
docker compose ps
```

Do not fix this by deleting the PostgreSQL volume. If `postgres_data` already
exists, changing `.env` does not change the database user's stored password; the
VM `.env` must match the existing database password unless the database password
is intentionally changed.

## Daily and Weekly Operation

Daily quick check:

```sh
cd /home/ubuntu/kukai_bot
docker compose ps
docker compose logs --tail 80 bot
ls -lh /home/ubuntu/kukai_backups | tail
```

Expected:

- `db` is healthy.
- `bot` is up.
- no repeating exception in the recent bot log.
- latest backup file is present after the scheduled backup time.

Weekly check:

```sh
df -h
docker system df
crontab -l
sudo systemctl status docker --no-pager
sudo systemctl status cron --no-pager
```

Also check the OCI console:

- Budget alerts have not fired unexpectedly.
- Instance shape is still within the A1 quota plan.
- No accidental load balancer, extra block volume, or reserved public IP exists.
- Security list ingress is still limited to SSH.

After each important Discord operation, prefer checking the bot log if behavior
looks unusual:

```sh
cd /home/ubuntu/kukai_bot
docker compose logs --tail 200 bot
```

## Persistent Button Smoke Test

Use this after changes to public Discord buttons or startup registration.

Important: buttons posted before persistent-view support was deployed do not have the required `custom_id`.
Use messages posted by the current code.
If a fresh entry-point button is needed without changing state, use `/kukai button`.

1. Start the bot with the current code.
2. Create or use a test kukai and post at least one public entry-point button:
   - `エントリーする`
   - `投句する`
   - `選句する`
   - `結果を見る`
3. Restart only the bot:

```powershell
docker compose restart bot
```

4. Without reposting the message, click the same button after the bot is ready.

Expected:

- The button opens the relevant modal/UI when the kukai is still in the matching state.
- The `エントリーする` button opens the haigo modal immediately. If the kukai is no longer accepting entries, the error is returned when the modal is submitted.
- For `投句する` and `選句する`, if the kukai has already moved past that stage, the bot replies with the current-state error instead of Discord showing an interaction failure.
- The result button shows the result view when the kukai is in `results` or `ended`.
- `/kukai button kind:current` reposts the action button for the current stage. Use `kind:result` to repost `結果を見る` after results are public.

Check the bot log for startup registration:

```text
Registered persistent kukai views: <count>
```

If the click shows Discord's generic interaction failure, confirm that the message was posted after this feature was deployed and that the startup log contains the registration line.

## Backups

For now, keep the old SQLite files as read-only rollback/reference data:

- `data/kukai.db`
- `data/kukai_scheduler.db`

PostgreSQL data lives in the Docker volume `kukai_bot_postgres_data`.

Create a PostgreSQL dump:

```sh
docker compose exec db pg_dump -U kukai -d kukai > kukai_backup.sql
```

Production daily backup is registered in the `ubuntu` user's crontab:

```cron
0 3 * * * cd /home/ubuntu/kukai_bot && docker compose exec -T db pg_dump -U kukai -d kukai > /home/ubuntu/kukai_backups/kukai_$(date +\%F_\%H\%M).sql 2>> /home/ubuntu/kukai_backups/backup.log
30 3 * * * find /home/ubuntu/kukai_backups -name 'kukai_*.sql' -mtime +30 -delete
```

Because the VM timezone is `Asia/Tokyo`, this runs at 03:00 JST and keeps about
30 days of dumps.

Confirm cron is installed and running:

```sh
sudo systemctl status cron --no-pager
crontab -l
```

Test the backup command manually:

```sh
cd /home/ubuntu/kukai_bot && docker compose exec -T db pg_dump -U kukai -d kukai > /home/ubuntu/kukai_backups/test_cron.sql 2>> /home/ubuntu/kukai_backups/backup.log
ls -lh /home/ubuntu/kukai_backups
rm /home/ubuntu/kukai_backups/test_cron.sql
```

The VM-local dump protects against application mistakes, but it does not protect
against VM or boot-volume loss. The next infrastructure improvement is to copy an
encrypted dump to external storage such as OCI Object Storage or another trusted
machine.

## Restore Drill

Use a separate VM or a clearly disposable Compose project for restore drills.
Do not test restore by overwriting the production volume.

Example restore flow for a disposable environment:

```sh
cd /home/ubuntu/kukai_bot
docker compose down
docker volume create kukai_restore_postgres_data
```

Adapt `docker-compose.yml` or a temporary override to point PostgreSQL at the
restore volume, start only the database, then restore:

```sh
docker compose up -d db
cat /home/ubuntu/kukai_backups/<backup-file>.sql | docker compose exec -T db psql -U kukai -d kukai
docker compose exec db psql -U kukai -d kukai -c "select count(*) from kukais;"
```

For production recovery, stop the bot before restoring database state.

## Troubleshooting

If the bot cannot resolve Discord hosts:

```sh
docker compose exec bot python -c "import socket; print(socket.gethostbyname('gateway.discord.gg'))"
```

If DNS fails repeatedly, restart the bot first, then the stack, then Docker Desktop.

If Compose reports a missing network, recreate the stack network:

```sh
docker compose down --remove-orphans
docker compose up -d
```

If the bot is not running after a VM reboot:

```sh
sudo systemctl status docker --no-pager
cd /home/ubuntu/kukai_bot
docker compose ps
docker compose logs --tail 100 bot
```

If PostgreSQL is not healthy:

```sh
cd /home/ubuntu/kukai_bot
docker compose logs --tail 100 db
docker compose restart db
docker compose restart bot
```

If Discord commands are missing after startup:

- If `DEV_GUILD_IDS` is empty, the bot uses global command sync and Discord may
  take up to about an hour to show changes.
- If fast test sync is needed, set `DEV_GUILD_IDS` to the test guild ID and
  restart the bot.
- Do not run two bot runtimes with the same token at the same time.

## PDF Font Checks

PDF generation uses LuaLaTeX inside the bot container. Emoji rendering requires
`Noto Color Emoji` in the image.

Confirm the emoji font is available:

```sh
docker compose exec bot fc-match "Noto Color Emoji"
```

Expected output includes:

```text
NotoColorEmoji.ttf: "Noto Color Emoji" "Regular"
```

If it falls back to another font such as `DejaVu Sans`, rebuild the bot image:

```sh
docker compose build bot
docker compose up -d bot
```

Already generated PDFs are not rewritten. Run `/pdf submission` or `/pdf result`
again after rebuilding.

## Discord Interaction Loading Text

The Discord client owns the temporary loading text such as `Kukai_bot が考え中...`.
Bots cannot customize that exact wording.

Ways to reduce how often users see it:

- Send an immediate response when work is cheap.
- Use modals for input-first flows. Public entry buttons should show the entry modal before DB work, then validate on modal submit.
- Keep `defer()` only for operations that may exceed Discord's initial response timeout.
- After `defer()`, replace the original response quickly with `edit_original_response()`.

Long-running PDF generation, result rendering, and DB/API-heavy button actions should still use `defer()` so the interaction does not expire.
