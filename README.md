<img align="right" width="220" alt="pg_smartcheck mascot" src="images/pg_smartcheck_mascot.png" />

# pg_smartcheck

`pg_smartcheck` is a comprehensive PostgreSQL database health analysis and performance diagnostics utility. It examines database activity, configuration, connection usage, checkpoints, background writer behavior, indexes, large objects, disk usage, table and index bloat, maintenance indicators, and replication status.

The goal is to give DBAs a consolidated view of potential database health and performance concerns, along with diagnostic details that help identify where further investigation may be warranted.

(c) 2026 SQLEXEC LLC

<div style="clear: right;"></div>

## Requirements

- PostgreSQL 10 or newer.
- Python 3.
- The PostgreSQL `psql` client available in the system `PATH`.
- The PostgreSQL `vacuumlo` utility available in the PostgreSQL binary directory for the orphaned large object check.
- Database credentials supplied through `.pgpass`, the `PGPASSWORD` environment variable, or PostgreSQL/libpq authentication configuration.

The utility supports Windows and Linux. Certain operating-system-specific checks are available only on local POSIX systems. Some checks depend on operating system access or PostgreSQL version-specific statistics and may report `INFO` when they cannot be evaluated.

## History

The first version of `pg_smartcheck` was created in November 2026 by consolidating functionality from my original `pg_report` and `pg_check` utilities.

## Overview

`pg_smartcheck` performs a series of read-only diagnostic checks against a PostgreSQL database. It uses the `psql` command-line client to collect PostgreSQL statistics and configuration information, and uses operating-system commands or filesystem information for checks that are available only when the database host is local.

The utility does not perform database maintenance. It does not execute `VACUUM`, `ANALYZE`, `FREEZE`, `REINDEX`, or other corrective operations. Its purpose is to identify conditions that may deserve attention and provide information to help a DBA decide what action, if any, is appropriate.

The program reports each check with a status:

- **OK**: The check completed and did not identify a condition that crossed its warning criteria.
- **WARNING**: The check identified a condition that may require investigation.
- **INFO**: The check is informational, not applicable, or could not be evaluated in the current environment.
- **VERB**: Additional verbose, diagnostic output displayed when `-v` is specified.

Some checks are based on statistical estimates and heuristic thresholds. A warning should be treated as a diagnostic indicator, not as proof that a database problem exists. Review the accompanying details and workload context before taking action.

## Health Checks

The following checks are performed by `pg_smartcheck`.

| Check | Description |
|---|---|
| Waits and locks | Identifies active queries waiting on events, excluding `DataFileRead`, when their wait duration exceeds the configured threshold. Reports waiting query information and blocking relationships when available. |
| Idle in transaction | Identifies sessions that have remained `idle in transaction` longer than the configured number of minutes. |
| Idle connections | Identifies idle client connections that have remained inactive longer than the configured threshold. |
| Long-running queries | Identifies non-idle queries running longer than the configured number of minutes. |
| Operating system load | On a local POSIX host, compares the 1-, 5-, and 15-minute load averages with 90% of the available CPU count. |
| Active connections | Compares active and idle-in-transaction sessions with a threshold of 2.5 sessions per vCPU. For remote databases, a vCPU count must be supplied. |
| Maximum connections | Calculates client connection utilization against usable connection capacity, accounting for reserved connections. A warning is raised at 70% utilization or higher. |
| Database conflicts and temporary files | Reports database conflicts, deadlocks, temporary file counts, and temporary bytes. Conflicts and deadlocks raise a warning; temporary file usage raises a warning when it exceeds 1 GB and there are no conflicts or deadlocks. |
| Cache hit ratio | Reports the current database cache hit ratio and warns when it is below 90%. |
| Checkpoints | Evaluates checkpoint frequency, requested versus timed checkpoints, checkpoint write pacing and synchronization time, WAL generation relative to `max_wal_size`, and current checkpointer/background-writer wait events where available. |
| Background writer | Evaluates client-backend, background-writer, and checkpointer write activity, backend fsync queue-full events, and background-writer cleaning-limit activity. The implementation supports PostgreSQL 15 and PostgreSQL 18; other versions report this check as informational. |
| Unused indexes | Identifies valid, ready, non-primary, non-unique, non-exclusion indexes larger than 8 KB that have no recorded scans or tuple activity. |
| Idle connection age | Reports active, idle-in-transaction, and idle client connection counts, connection creation activity, and average idle connection age. Raises a warning when 25% or more of idle connections have been inactive for over 1 hour. |
| Orphaned large objects | Uses `vacuumlo` in dry-run mode to identify large objects that would be removed. It never removes large objects. This check is not available on standby servers. |
| Data directory usage | On a local non-RDS host, reports filesystem usage for the PostgreSQL data directory and warns when usage exceeds 75%. |
| Table bloat | Estimates table and associated TOAST space usage. Reports tables with estimated bloat greater than 30% and estimated excess space greater than 100 MB. |
| TOAST tables | Reports TOAST relation statistics, including physical size, live and dead tuples, and FSM free-space information when available. Helps identify TOAST storage that may warrant further investigation. |
| Index bloat | Estimates B-tree index bloat. Reports indexes larger than 50 MB with estimated bloat greater than 30%. |
| Maintenance candidates | Reviews table statistics, dead-tuple and modification ratios, maintenance history, and transaction ID age to identify tables that may warrant maintenance attention. This is diagnostic only and does not run maintenance. |
| Replication | Checks replication status and lag, using the built-in replication lag thresholds where applicable. |
| Replication slot health | Reports replication slot details, including slot type, database, activity, retained WAL, WAL status, and transaction ID horizons. Warns about lost or extended WAL reservations, exhausted safe WAL retention, and slot horizons approaching transaction ID freeze limits. |
| XID and MultiXact health | Checks transaction ID and MultiXact ages across databases. Raises warnings at 80% and critical alerts at 95% of the configured freeze limits. |
| Sequence exhaustion | Identifies sequences approaching their maximum or minimum values, supporting both ascending and descending sequences. Raises warnings at 80% and critical alerts at 95%. Also reports unavailable current values and cycle-enabled sequences. |
| Prepared transactions | Identifies outstanding prepared transactions and reports their GID, database, owner, preparation timestamp, and age. Raises a warning whenever prepared transactions are present. |
| Invalid indexes | Identifies indexes that are invalid or not ready. Reports index validity, readiness, liveness, uniqueness, and definition to assist with investigation and possible reindexing. |
| WAL archiving health | Reports WAL archiving configuration and activity, including successful and failed archive counts, the latest archived files, and failure information. Identifies missing configuration and unresolved archiving failures. |
| Replication configuration | Reviews replication-related configuration, including `wal_level`, `max_wal_senders`, `max_replication_slots`, synchronous replication, and `hot_standby`. |
| Foreign key indexing | Identifies foreign keys without a suitable supporting B-tree index on the referencing table. Considers index validity, readiness, partial indexes, expression indexes, and leading key column order. |
| Unlogged tables | Identifies user-defined unlogged tables. Reports them as informational findings because their data is not WAL-logged, is not replicated to physical standbys, and is truncated after a crash. |
| Connection churn | Reports cumulative sessions established and the average session creation rate per hour. Requires PostgreSQL 14 or newer. |
| Configuration recommendations | Reviews important PostgreSQL configuration settings, including autovacuum, checkpoint pacing, data checksums, logging, idle transaction timeout, pg_stat_statements, and query-text tracking capacity. |


### Important notes about the checks

- **Local versus remote access:** Operating system load and data-directory filesystem usage require access to the PostgreSQL host. They are reported as `INFO` when the database is remote. RDS data-directory usage is not available through this check.
- **CPU count:** The active connection check uses the detected CPU count for a local database host. For a remote database, specify `--vcpus` to enable this comparison. The operating system load check is available only on a local POSIX host.
- **PostgreSQL version differences:** Some statistics moved between PostgreSQL system views in newer releases. The checkpoint check accounts for the PostgreSQL 17 change, while the background writer check currently has explicit support for PostgreSQL 15 and 18.
- **Bloat estimates:** Table and index bloat calculations are estimates based on catalog statistics and page/tuple assumptions. They are useful for identifying candidates for closer inspection, but are not exact measurements of reclaimable disk space.
- **Statistics reset:** PostgreSQL statistics are cumulative since their last reset. Interpret counters and rates with the corresponding statistics reset time in mind.
- **Unused indexes:** An index with zero recorded scans is not automatically safe to drop. Consider application behavior, infrequent reporting, constraints, and the period covered by the statistics before making changes.
- **Orphaned large objects:** The check invokes `vacuumlo` with its `-n` dry-run option. The utility only reports what `vacuumlo` would remove.

## Command-Line Parameters

The following parameters are supported.

| Parameter | Description | Default |
|---|---|---|
| `-h --dbhost` | Database host name or IP address. If omitted, libpq's local default connection behavior is used. | Local default |
| `-p --port` | Database server port. | `5432` |
| `-U --dbuser` | Database user. | libpq default |
| `-d --database` | Database name. Required. | None |
| `-w --waitslocks` | Wait/lock threshold in seconds. A value less than or equal to zero disables this check. | Disabled |
| `-l --longquerymins` | Long-running query threshold in minutes. A value less than or equal to zero disables this check. | Disabled |
| `-c --vcpus` | Available CPU count. Used by CPU-related checks, particularly for remote database hosts. | Auto-detected locally; otherwise unspecified |
| `-i --idleintransmins` | Idle-in-transaction threshold in minutes. A value less than or equal to zero disables this check. | Disabled |
| `-o --idleconnmins` | Idle connection threshold in minutes. | `240` |
| `--html` | Generate an HTML report in addition to console output. | Disabled |
| `-v --verbose` | Display additional diagnostic information. | Disabled |
| `-b --debug` | Display debug information, including executed commands and command results. | Disabled |

The database name is the only required command-line parameter. Host, port, and user values may also be supplied through the normal libpq environment and service configuration.

## Default Diagnostic Thresholds

Several checks use fixed thresholds in the current implementation.

| Check | Threshold |
|---|---:|
| Maximum connection utilization | 70% of usable connection capacity |
| Active connections | 2.5 sessions per vCPU |
| Cache hit ratio | Warning below 90% |
| OS load | Warning when load average exceeds 90% of the available CPU count |
| Idle connection recent activity | No activity in the previous 1 hour |
| Data directory usage | Warning above 75% |
| Table bloat | Greater than 30% and greater than 100 MB estimated excess |
| Index bloat | Index larger than 50 MB and greater than 30% estimated bloat |
| Temporary file usage | Warning above 1 GB when there are no conflicts or deadlocks |
| Replication lag | 30 seconds or 256 MB, as applicable |
| Idle connection age | Warning when 25% or more of idle connections have been inactive for over 1 hour |
| Checkpoint interval | Warning when average interval is below 2 minutes or at least 30 minutes |
| Checkpoint timeout | Warning when checkpoint_timeout is below 2 minutes or exceeds 30 minutes |
| Background writer | Warning for backend fsync queue-full events, backend writes exceeding background-writer and checkpointer writes combined, or repeated cleaning-limit hits with substantial capacity usage |
| Configuration recommendations | Warning for any unmet configuration recommendation |




The wait/lock, long-running query, and idle-in-transaction checks are disabled unless their respective thresholds are supplied on the command line.

## HTML Report

Use `--html` to generate an HTML report containing a summary of the checks and their results. The report includes:

- Database host, database name, PostgreSQL version, and generation timestamp.
- Total check count.
- Counts for `OK`, `WARNING`, and `INFO` results.
- A health-check table with the status and analysis for each check.
- Expandable detail sections when a check provides additional diagnostic information.

The report is written to the current working directory using this filename format:

```text
pg_smartcheck_YYYYMMDD_HHMM.html
```

For example:

```text
pg_smartcheck_20261105_1430.html
```

## Invocation

General syntax:

```text
python pg_smartcheck.py -d <database> [options]
```

The `psql` client must be available in the `PATH`. Authentication should be configured before running the utility, preferably through `.pgpass` or the normal PostgreSQL/libpq authentication mechanisms.

### Basic execution

Connect to a local database using the default port and user:

```bash
python3 pg_smartcheck.py -d testing
```

Connect to a database on another host:

```bash
python3 pg_smartcheck.py -h 192.168.0.100 -p 5432 -U postgres -d testing
```

### Enable the wait and lock check

Report active queries waiting longer than 30 seconds:

```bash
python3 pg_smartcheck.py -d testing -w 30
```

### Enable the long-running query check

Report non-idle queries running longer than 5 minutes:

```bash
python3 pg_smartcheck.py -d testing -l 5
```

### Enable the idle-in-transaction check

Report sessions that have remained idle in transaction longer than 10 minutes:

```bash
python3 pg_smartcheck.py -d testing -i 10
```

### Specify the CPU count

Provide the number of vCPUs available to a remote database server:

```bash
python3 pg_smartcheck.py -h dbserver.example.com -U postgres -d testing -c 8
```

### Generate an HTML report

Run all checks and generate an HTML report:

```bash
python3 pg_smartcheck.py -d testing --html
```

### Enable verbose output

Display additional diagnostic information:

```bash
python3 pg_smartcheck.py -d testing -v
```

### Enable debug output

Display executed commands and their output:

```bash
python3 pg_smartcheck.py -d testing -b
```

## Scheduling

`pg_smartcheck` can be run periodically through cron on Linux or through Task Scheduler on Windows. For example, the following cron entry runs the utility every day at 6:15 AM and appends console output to a log file:

```cron
15 6 * * * /usr/bin/python3 /path/to/pg_smartcheck.py -d testing >> /var/log/pg_smartcheck.log 2>&1
```

Adjust the executable path, script path, database name, authentication configuration, and schedule for your environment.

## Design Philosophy

`pg_smartcheck` is intended to be a practical diagnostic companion for PostgreSQL DBAs. It brings a broad collection of health and performance checks into one execution, highlights conditions that may deserve attention, and provides supporting details without changing the database.

The checks are deliberately diagnostic. A warning is a starting point for investigation, not an instruction to make an immediate configuration or maintenance change. Database workload, application behavior, statistics history, PostgreSQL version, and hosting environment all matter when interpreting the results.

## Future Enhancements

- Integrate PgBouncer monitoring using the retained `pgbouncer_pkg` functionality.

## Sample Output

```
[INFO]  pg_smartcheck  version: 1.0  November 06, 2026     Python Version: 3     PG Database: mydb
[INFO]  OS load check not applicable for remote DB instances.
[INFO]  N/A: PostgreSQL host is remote. Server filesystem usage is unavailable.
[WARN]  3 PostgreSQL configuration recommendation(s) need attention.
[WARN]  checkpoint_completion_target is less than optimal (value=0.7).
[WARN]  idle_in_transaction_session_timeout is turned off.
[WARN]  track_activity_query_size may need to be increased; query text may be truncated (value=1024). Changing this setting requires a PostgreSQL restart.
[ OK ]  Sessions established: 2,545,304  Average session creation rate: 1314.91 per hour.  Statistics reset: 2026-08-17 23:08:07
[ OK ]  No waiting/blocked queries longer than 10 seconds were detected.
[ OK ]  No "idle in transaction" sessions longer than 10 minute(s) were detected.
[ OK ]  No idle connections older than 240 minute(s) were detected.
[ OK ]  No long-running queries longer than 1 minute(s) were detected.
[ OK ]  Active connection load is within threshold: active_sessions=1  threshold=20.0  vCPUs=8
[ OK ]  Active: 1.  Idle in Trans: 1.  37 current connections.  Started (last 2 mins): 2.  Started (last 5 mins): 8.  Started (last 15 mins): 13.
[ OK ]  35 idle connections and 34 of them have recent activity.  Average idle age: 0.02 days (33 minutes).
[ OK ]  PostgreSQL connection utilization: 38/497 (7.6%)  max_connections=500
[ OK ]  High cache hit ratio: 99.99% (blocks hit vs blocks read)  blocks_read=308226179  blocks_hit=5889090632217
[WARN]  Database activity requires investigation: conflicts=0  deadlocks=0  temp_files=177  temp_bytes=9.59 GB  stats_reset=N/A
[ OK ]  Checkpoint activity is healthy. Checkpoints are occurring at the expected interval without excessive WAL-driven activity.
[ OK ]  Background writer activity is healthy. Client backend writes account for only 1.9% of total buffer writes.
[WARN]  39 unused indexes were found.
[ OK ]  No orphaned large objects were found.
[ OK ]  No bloated tables detected.
[ OK ]  No bloated indexes detected.
[WARN]  Maintenance candidates detected: VACUUM/ANALYZE: 81, VACUUM: 4, ANALYZE: 4, FREEZE: 0
[ OK ]  1 streaming replication connection(s) detected. No significant replication lag detected.
[INFO]  HTML report generated: C:\programdirectory\pg_smartcheck_20261106_0951.html
```



