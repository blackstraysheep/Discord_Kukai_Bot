# Operations Runbook

## Standard PostgreSQL Operation

Use the PostgreSQL compose override for normal operation:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

Follow bot logs:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml logs -f bot
```

Stop the stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml down
```

Do not use `down -v` unless intentionally deleting the PostgreSQL database volume.

## Health Checks

Confirm Alembic is at the latest revision:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec bot alembic current
```

Confirm the database is reachable:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec db psql -U kukai -d kukai -c "select count(*) from kukais;"
```

Confirm the select comment schema is fully renamed:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec db psql -U kukai -d kukai -c "\d select_comments"
```

Expected:

- Alembic head is `0014_select_comments_select_id`
- `select_comments` has `select_id`
- `select_comments` does not have `vote_id`

## Restarting

Restart only the bot:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml restart bot
```

Restart the full stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml down
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

## Backups

For now, keep the old SQLite files as read-only rollback/reference data:

- `data/kukai.db`
- `data/kukai_scheduler.db`

PostgreSQL data lives in the Docker volume `kukai_bot_postgres_data`.

Create a PostgreSQL dump:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec db pg_dump -U kukai -d kukai > kukai_backup.sql
```

## Troubleshooting

If the bot cannot resolve Discord hosts:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec bot python -c "import socket; print(socket.gethostbyname('gateway.discord.gg'))"
```

If DNS fails repeatedly, restart the bot first, then the stack, then Docker Desktop.

If Compose reports a missing network, recreate the stack network:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml down --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

## Discord Interaction Loading Text

The Discord client owns the temporary loading text such as `Kukai_bot が考え中...`.
Bots cannot customize that exact wording.

Ways to reduce how often users see it:

- Send an immediate response when work is cheap.
- Use modals for input-first flows.
- Keep `defer()` only for operations that may exceed Discord's initial response timeout.
- After `defer()`, replace the original response quickly with `edit_original_response()`.

Long-running PDF generation, result rendering, and DB/API-heavy button actions should still use `defer()` so the interaction does not expire.
