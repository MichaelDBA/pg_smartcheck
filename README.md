<img align="right" width="220" alt="pg_smartvac mascot" src="images/pg_smartcheck_mascot.png" />

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

The utility supports Windows and Linux/POSIX environments. Some checks depend on operating system access or PostgreSQL version-specific statistics and may report `INFO` when they cannot be evaluated.

## History

The first version of `pg_smartcheck` was created in November 2026 by consolidating functionality from the original `pg_report` and `pg_check` utilities.

## Overview

`pg_smartcheck` performs a series of read-only diagnostic checks against a PostgreSQL database. It uses the `psql` command-line client to collect PostgreSQL statistics and configuration information, and uses operating-system commands or filesystem information for checks that are available only when the database host is local.

The utility does not perform database maintenance. It does not execute `VACUUM`, `ANALYZE`, `FREEZE`, `REINDEX`, or other corrective operations. Its purpose is to identify conditions that may deserve attention and provide information to help a DBA decide what action, if any, is appropriate.

The program reports each check with a status:

- **OK**: The check completed and did not identify a condition that crossed its warning criteria.
- **WARNING**: The check identified a condition that may require investigation.
- **INFO**: Provides additional information.
- **VERB**: Shows verbose information if **-v** is provided.

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
| Background writer | Compares background-writer activity with client-backend writing. The implementation supports PostgreSQL 15 and PostgreSQL 18; other versions report this check as informational. |
| Unused indexes | Identifies valid, ready, non-primary, non-unique, non-exclusion indexes larger than 8 KB that have no recorded scans or tuple activity. |
| Idle connection age | Reports the number and average age of idle client connections, and identifies connections without activity in the previous hour. |
| Orphaned large objects | Uses `vacuumlo` in dry-run mode to identify large objects that would be removed. It never removes large objects. This check is not available on standby servers. |
| Data directory usage | On a local non-RDS host, reports filesystem usage for the PostgreSQL data directory and warns when usage exceeds 75%. |
| Table bloat | Estimates table and associated TOAST space usage. Reports tables with estimated bloat greater than 30% and estimated excess space greater than 100 MB. |
| Index bloat | Estimates B-tree index bloat. Reports indexes larger than 50 MB with estimated bloat greater than 30%. |
| Maintenance candidates | Reviews table statistics, dead-tuple and modification ratios, maintenance history, and transaction ID age to identify tables that may warrant maintenance attention. This is diagnostic only and does not run maintenance. |
| Replication | Checks replication status and lag, using the configured lag thresholds where applicable. |

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
| OS load | Warning above 90% of available CPU capacity |
| Idle connection age | 240 minutes by default |
| Idle connection recent activity | No activity in the previous 1 hour |
| Data directory usage | Warning above 75% |
| Table bloat | Greater than 30% and greater than 100 MB estimated excess |
| Index bloat | Index larger than 50 MB and greater than 30% estimated bloat |
| Temporary file usage | Warning above 1 GB when there are no conflicts or deadlocks |
| Replication lag | 30 seconds or 256 MB, as applicable |

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

<img width="1689" height="399" alt="image" src="https://github.com/user-attachments/assets/12db6d6f-2203-407e-942c-4dafa5b4b24f" />



