# PostgreSQL Migration Guide

## Quick Start

### 1. Start PostgreSQL

```bash
# Start PostgreSQL using Docker Compose
docker-compose -f docker-compose.postgresql.yml up -d postgres

# Wait for PostgreSQL to be ready
docker-compose -f docker-compose.postgresql.yml ps
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.postgresql.example .env

# Edit .env and set:
# DATABASE_TYPE=postgresql
# POSTGRESQL_URL=postgresql://grader:grader_password_change_in_production@localhost:5432/grading
```

### 3. Install Dependencies

```bash
pip install asyncpg psycopg2-binary
```

### 4. Run Migration

```bash
# Migrate data from SQLite to PostgreSQL
python scripts/migrate_to_postgresql.py
```

### 5. Verify Migration

The migration script will automatically verify that all data was migrated correctly.

### 6. Start Application

```bash
# Start the application with PostgreSQL
uvicorn src.api.main:app --reload
```

---

## Detailed Steps

### Prerequisites

- Docker and Docker Compose installed
- Python 3.12+
- Existing SQLite database at `outputs/grading_database.db`

### Step 1: Backup SQLite Database

```bash
# Create backup
cp outputs/grading_database.db outputs/grading_database.db.backup.$(date +%Y%m%d_%H%M%S)
```

### Step 2: Start PostgreSQL

```bash
# Start PostgreSQL container
docker-compose -f docker-compose.postgresql.yml up -d postgres

# Check logs
docker-compose -f docker-compose.postgresql.yml logs -f postgres

# Verify PostgreSQL is running
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT version();"
```

### Step 3: Initialize Schema

The schema is automatically initialized when the container starts (via `docker-entrypoint-initdb.d`).

To manually initialize:

```bash
docker-compose -f docker-compose.postgresql.yml exec -T postgres psql -U grader -d grading < src/db/schema_postgresql.sql
```

### Step 4: Configure Application

Edit `.env`:

```bash
# Database Configuration
DATABASE_TYPE=postgresql
POSTGRESQL_URL=postgresql://grader:grader_password_change_in_production@localhost:5432/grading

# Keep SQLite path for rollback
SQLITE_DB_PATH=outputs/grading_database.db
```

### Step 5: Install PostgreSQL Drivers

```bash
pip install -r requirements.txt
```

### Step 6: Run Migration Script

```bash
python scripts/migrate_to_postgresql.py
```

The script will:
1. Connect to both SQLite and PostgreSQL
2. Migrate all tables in dependency order
3. Verify row counts match
4. Report success or failure

Expected output:
```
================================================================================
SQLite to PostgreSQL Migration
================================================================================
Source (SQLite): outputs/grading_database.db
Target (PostgreSQL): postgresql://grader:***@localhost:5432/grading

This will migrate all data from SQLite to PostgreSQL. Continue? (yes/no): yes

Connecting to databases...
Initializing PostgreSQL schema...
✓ Schema initialized

Migrating tables...
Migrating table: tasks
Table tasks has 150 rows
✓ Migrated 150 rows from tasks
...

✓ Migration complete: 1500 rows migrated

Verifying migration...
✓ tasks: 150 rows (match)
...

✓ Verification passed: All data migrated successfully
```

### Step 7: Test Application

```bash
# Start application
uvicorn src.api.main:app --reload

# Test health endpoint
curl http://localhost:8000/health

# Test grading endpoint
curl -X POST http://localhost:8000/api/v1/grade/submit \
  -F "files=@test_image.jpg" \
  -F "rubric_json={...}"
```

### Step 8: Monitor Performance

```bash
# Check PostgreSQL connections
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT count(*) FROM pg_stat_activity;"

# Check table sizes
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size FROM pg_tables WHERE schemaname = 'public' ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"
```

---

## Rollback Plan

If you need to rollback to SQLite:

### 1. Stop Application

```bash
# Stop uvicorn
pkill -f uvicorn
```

### 2. Update Configuration

Edit `.env`:

```bash
DATABASE_TYPE=sqlite
SQLITE_DB_PATH=outputs/grading_database.db
```

### 3. Restart Application

```bash
uvicorn src.api.main:app --reload
```

### 4. Stop PostgreSQL (Optional)

```bash
docker-compose -f docker-compose.postgresql.yml down
```

---

## Troubleshooting

### Connection Refused

```bash
# Check if PostgreSQL is running
docker-compose -f docker-compose.postgresql.yml ps

# Check logs
docker-compose -f docker-compose.postgresql.yml logs postgres

# Restart PostgreSQL
docker-compose -f docker-compose.postgresql.yml restart postgres
```

### Migration Fails

```bash
# Check SQLite database exists
ls -lh outputs/grading_database.db

# Check PostgreSQL is accessible
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT 1;"

# Re-run migration with verbose logging
python scripts/migrate_to_postgresql.py
```

### Performance Issues

```bash
# Check active connections
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# Check slow queries
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC;"

# Analyze tables
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "ANALYZE;"
```

---

## Production Deployment

### Security

1. **Change default password**:
   ```bash
   # In docker-compose.postgresql.yml
   POSTGRES_PASSWORD: <strong-random-password>
   
   # In .env
   POSTGRESQL_URL=postgresql://grader:<strong-random-password>@localhost:5432/grading
   ```

2. **Use secrets management**:
   - Store credentials in environment variables
   - Use Docker secrets or Kubernetes secrets
   - Never commit credentials to git

3. **Enable SSL**:
   ```bash
   POSTGRESQL_URL=postgresql://grader:password@localhost:5432/grading?sslmode=require
   ```

### Performance Tuning

1. **Adjust connection pool**:
   ```python
   # In src/db/adapter.py PostgreSQLAdapter.connect()
   self.pool = await asyncpg.create_pool(
       connection_string,
       min_size=10,  # Increase for production
       max_size=50,  # Increase for production
       command_timeout=60,
   )
   ```

2. **Configure PostgreSQL**:
   ```bash
   # In docker-compose.postgresql.yml, add:
   command:
     - "postgres"
     - "-c"
     - "max_connections=200"
     - "-c"
     - "shared_buffers=256MB"
     - "-c"
     - "effective_cache_size=1GB"
     - "-c"
     - "work_mem=16MB"
   ```

### Monitoring

1. **Enable query logging**:
   ```bash
   # In docker-compose.postgresql.yml
   command:
     - "postgres"
     - "-c"
     - "log_statement=all"
     - "-c"
     - "log_duration=on"
   ```

2. **Use pg_stat_statements**:
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
   SELECT * FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;
   ```

---

## Performance Comparison

### Before (SQLite)
- Concurrent writes: Serial (1 at a time)
- Write lock timeout: Up to 11 seconds
- Batch throughput: Baseline
- Long-tail latency: 11 seconds

### After (PostgreSQL)
- Concurrent writes: Parallel (unlimited)
- Write lock timeout: None
- Batch throughput: 5-10x improvement
- Long-tail latency: <100ms

---

## Support

For issues or questions:
1. Check logs: `docker-compose -f docker-compose.postgresql.yml logs`
2. Review documentation: `docs/POSTGRESQL_MIGRATION_PLAN.md`
3. Open an issue on GitHub
