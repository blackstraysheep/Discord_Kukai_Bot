# PostgreSQL Migration

This migration is staged so the default SQLite setup keeps working.

## Stage 1: Boot a fresh PostgreSQL database

Start the bot with the PostgreSQL override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

The override sets:

```env
DATABASE_URL=postgresql+asyncpg://kukai:kukai@db:5432/kukai
SCHEDULER_DB_URL=postgresql+psycopg://kukai:kukai@db:5432/kukai
```

`DATABASE_URL` uses the async driver for the bot runtime. `SCHEDULER_DB_URL` uses a sync driver because APScheduler's SQLAlchemy job store is synchronous.

## Stage 2: Verify migrations

The container runs:

```sh
alembic upgrade head && python -m bot.main
```

Before migrating real data, confirm that a fresh PostgreSQL database reaches Alembic head and the bot starts.

## Stage 3: Move existing data

For existing SQLite data, export/import with a dedicated migration tool such as `pgloader`, then run:

```sh
alembic upgrade head
```

After import, verify at least:

- active kukai rows and states
- entries, submissions, selects, and comments
- notification schedules and logs
- APScheduler jobs, or intentionally rebuild them from notification schedules

## Stage 4: Cut over

Once imported data is verified, keep PostgreSQL URLs in `.env` and start with the PostgreSQL compose override. Keep the SQLite files as rollback backups until a full event cycle has completed.
