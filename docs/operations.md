# Operations Runbook

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

```powershell
docker compose up -d
```

Follow bot logs:

```powershell
docker compose logs -f bot
```

Stop the stack:

```powershell
docker compose down
```

Do not use `down -v` unless intentionally deleting the PostgreSQL database volume.

## Health Checks

Confirm Alembic is at the latest revision:

```powershell
docker compose exec bot alembic current
```

Confirm the database is reachable:

```powershell
docker compose exec db psql -U kukai -d kukai -c "select count(*) from kukais;"
```

Confirm the select comment schema is fully renamed:

```powershell
docker compose exec db psql -U kukai -d kukai -c "\d select_comments"
```

Expected:

- Alembic head is `0014_select_comments_select_id`
- `select_comments` has `select_id`
- `select_comments` does not have `vote_id`

## Restarting

Restart only the bot:

```powershell
docker compose restart bot
```

Restart the full stack:

```powershell
docker compose down
docker compose up -d
```

Rebuild the bot image when Dockerfile dependencies, TeX packages, or fonts change:

```powershell
docker compose build bot
docker compose up -d bot
```

Equivalent one-shot rebuild:

```powershell
docker compose up -d --build bot
```

## Persistent Button Smoke Test

Use this after changes to public Discord buttons or startup registration.

Important: buttons posted before persistent-view support was deployed do not have the required `custom_id`.
Use messages posted by the current code.

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
- If the kukai has already moved past that stage, the bot replies with the current-state error instead of Discord showing an interaction failure.
- The result button shows the result view when the kukai is in `results` or `ended`.

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

```powershell
docker compose exec db pg_dump -U kukai -d kukai > kukai_backup.sql
```

## Troubleshooting

If the bot cannot resolve Discord hosts:

```powershell
docker compose exec bot python -c "import socket; print(socket.gethostbyname('gateway.discord.gg'))"
```

If DNS fails repeatedly, restart the bot first, then the stack, then Docker Desktop.

If Compose reports a missing network, recreate the stack network:

```powershell
docker compose down --remove-orphans
docker compose up -d
```

## PDF Font Checks

PDF generation uses LuaLaTeX inside the bot container. Emoji rendering requires
`Noto Color Emoji` in the image.

Confirm the emoji font is available:

```powershell
docker compose exec bot fc-match "Noto Color Emoji"
```

Expected output includes:

```text
NotoColorEmoji.ttf: "Noto Color Emoji" "Regular"
```

If it falls back to another font such as `DejaVu Sans`, rebuild the bot image:

```powershell
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
- Use modals for input-first flows.
- Keep `defer()` only for operations that may exceed Discord's initial response timeout.
- After `defer()`, replace the original response quickly with `edit_original_response()`.

Long-running PDF generation, result rendering, and DB/API-heavy button actions should still use `defer()` so the interaction does not expire.
