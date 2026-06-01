# PostgreSQL Migration

PostgreSQL is the current operational database for the bot.
SQLite remains useful for local in-memory tests and as historical backup data.

## Current Operation

Start the bot with the standard Compose file:

```powershell
docker compose up -d --build
```

The compose file sets PostgreSQL defaults, which can be overridden in `.env`:

```env
DATABASE_URL=postgresql+asyncpg://kukai:kukai@db:5432/kukai
SCHEDULER_DB_URL=postgresql+psycopg://kukai:kukai@db:5432/kukai
```

`DATABASE_URL` uses the async driver for the bot runtime. `SCHEDULER_DB_URL` uses a sync driver because APScheduler's SQLAlchemy job store is synchronous.

## Verify Migrations

The container runs:

```sh
alembic upgrade head && python -m bot.main
```

Confirm the running database is at head:

```powershell
docker compose exec bot alembic current
```

Expected head:

```text
0014_select_comments_select_id
```

## Existing SQLite Data

Existing SQLite files are not required for normal operation. Keep them as backup/reference data unless a specific historical kukai must be restored.

For existing SQLite data that must be imported, export/import with a dedicated migration tool such as `pgloader`, then run:

```sh
alembic upgrade head
```

After import, verify at least:

- active kukai rows and states
- entries, submissions, selects, and comments
- notification schedules and logs
- APScheduler jobs, or intentionally rebuild them from notification schedules

## Cutover Status

PostgreSQL cutover has been verified through:

- Alembic migration to `0014_select_comments_select_id`
- Discord operation cycle
- APScheduler notification delivery
- `select_comments.select_id` schema verification
