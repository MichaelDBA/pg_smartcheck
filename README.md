<img align="right" width="220" alt="pg_smartvac mascot" src="images/pg_smartcheck_mascot.png" />

# pg_smartcheck

`pg_smartcheck` is a comprehensive PostgreSQL database health analysis
utility that evaluates database configuration, performance, activity,
replication, storage, and maintenance conditions to identify potential
problems and provide DBAs with operational insight.

Unlike traditional monitoring scripts that focus on individual metrics
or isolated alerts, `pg_smartcheck` consolidates a broad range of
PostgreSQL health checks into a single utility, providing both console
output and detailed HTML reporting.

(c) 2026 SQLEXEC LLC

<div style="clear: right;"></div>

## Requirements

-   PostgreSQL 10 and later.
-   Python 3.
-   PostgreSQL `psql` client available in the user's PATH.
-   Database authentication through `.pgpass`, the `PGPASSWORD`
    environment variable, or PostgreSQL authentication configuration.

## History

The first version of this program was created in November 2026 by
consolidating functionality from the existing `pg_report.py` and
`pg_check.py` utilities.

## Overview

`pg_smartcheck` performs a series of PostgreSQL health checks and
reports conditions that may warrant further investigation.

It evaluates database activity, connection utilization, query behavior,
cache performance, checkpoint activity, background writer activity,
replication health, storage utilization, table and index bloat, and
maintenance candidates.

The program runs on Windows and Linux and uses the PostgreSQL
command-line client to retrieve database information.

It supports console output and optional HTML reporting. The HTML report
is written to the current working directory using the filename format:

`pg_smartcheck_YYYYMMDD_HHMM.html`

Here are the parameters:

  -----------------------------------------------------------------------
  Parameter                           Description
  ----------------------------------- -----------------------------------
  `-h --dbhost`                       Database host name or IP address
                                      (default: libpq default)

  `-d --database`                     Database name (required)

  `-U --dbuser`                       Database user (default: libpq
                                      default)

  `-p --port`                         Database port (default: 5432)

  `-w --waitslocks`                   Wait/lock threshold in seconds
                                      (default: disabled)

  `-l --longquerymins`                Long-running query threshold in
                                      minutes (default: disabled)

  `-c --vcpus`                        Number of available CPUs (default:
                                      not specified)

  `-i --idleintransmins`              Idle-in-transaction threshold in
                                      minutes (default: disabled)

  `-o --idleconnmins`                 Idle connection threshold in
                                      minutes (default: 240)

  `--html`                            Generate an HTML report

  `-v --verbose`                      Enable verbose output

  `-b --debug`                        Enable debug output
  -----------------------------------------------------------------------

## Why pg_smartcheck?

PostgreSQL provides extensive monitoring statistics and diagnostic
information, but DBAs often need to examine multiple system views and
configuration settings to develop a complete picture of database health.

`pg_smartcheck` brings these diagnostics together into one utility. Key
benefits include:

-   **Comprehensive health analysis:** Evaluates multiple aspects of
    PostgreSQL database health through a single execution.
-   **Performance diagnostics:** Identifies long-running queries, waits,
    connection pressure, cache performance, and checkpoint activity.
-   **Replication monitoring:** Evaluates streaming replication status,
    WAL receiver activity, and potential replication lag.
-   **Storage analysis:** Identifies potential table and index bloat,
    unused indexes, orphaned large objects, and filesystem utilization
    issues.
-   **Maintenance visibility:** Identifies tables that may need VACUUM,
    ANALYZE, or FREEZE attention.
-   **Operational awareness:** Examines database conflicts, deadlocks,
    background writer activity, and connection age.
-   **Detailed reporting:** Provides console output and an HTML report
    with expandable diagnostic details.

`pg_smartcheck` is strictly a diagnostic and reporting utility. It does
not execute VACUUM, ANALYZE, FREEZE, REINDEX, or other database
maintenance operations. It identifies potential issues and provides
information to help DBAs determine the appropriate response.

## Health Checks

`pg_smartcheck` currently performs the following checks:

  -----------------------------------------------------------------------
  Check                               Description
  ----------------------------------- -----------------------------------
  Waits and locks                     Identifies active queries waiting
                                      on database resources and reports
                                      blocking relationships.

  Idle in transaction                 Identifies sessions that have
                                      remained idle in transaction beyond
                                      the configured threshold.

  Idle connections                    Identifies idle connections that
                                      have exceeded the configured age
                                      threshold.

  Long-running queries                Identifies queries exceeding the
                                      configured execution-time
                                      threshold.

  Server load                         Evaluates operating-system load
                                      averages when local server
                                      information is available.

  Active connections                  Evaluates active connection
                                      activity relative to available CPU
                                      capacity.

  Maximum connections                 Evaluates connection utilization
                                      against the configured connection
                                      capacity.

  Cache hit ratio                     Evaluates the database buffer cache
                                      hit ratio.

  Database conflicts                  Reports database conflicts,
                                      deadlocks, and temporary-file
                                      activity.

  Checkpoints                         Evaluates checkpoint activity,
                                      timing, WAL generation, and related
                                      performance indicators.

  Background writer                   Examines background writer and
                                      client backend write activity.

  Unused indexes                      Identifies indexes that may be
                                      candidates for further
                                      investigation.

  Connection age                      Evaluates the age and activity
                                      history of database connections.

  Orphaned large objects              Identifies large objects that may
                                      no longer be referenced.

  Data directory usage                Evaluates filesystem utilization
                                      for locally accessible PostgreSQL
                                      data directories.

  Table bloat                         Estimates table bloat and
                                      identifies potentially excessive
                                      space usage.

  Index bloat                         Estimates index bloat and
                                      identifies indexes that may warrant
                                      further investigation.

  Maintenance candidates              Identifies tables that may need
                                      VACUUM, ANALYZE, or FREEZE
                                      attention.

  Replication                         Evaluates primary and standby
                                      replication status, WAL receiver
                                      activity, and replication lag.
  -----------------------------------------------------------------------

Some checks depend on operating-system visibility or PostgreSQL
version-specific statistics. When information is unavailable, the
utility reports the condition rather than treating missing information
as a confirmed database failure.

## Diagnostic Thresholds

Several checks use configurable command-line thresholds, allowing DBAs
to adjust monitoring behavior to their environment.

The following options control the primary activity-related checks:

  -----------------------------------------------------------------------
  Parameter                           Description
  ----------------------------------- -----------------------------------
  `--waitslocks`                      Minimum duration in seconds for a
                                      query waiting on a database
                                      resource to be reported.

  `--longquerymins`                   Minimum query duration in minutes
                                      before a query is reported.

  `--idleintransmins`                 Maximum idle-in-transaction
                                      duration in minutes before a
                                      session is reported.

  `--idleconnmins`                    Maximum idle connection duration in
                                      minutes before a connection is
                                      reported.

  `--vcpus`                           CPU count used by CPU-related
                                      diagnostic checks.
  -----------------------------------------------------------------------

Additional checks use built-in diagnostic thresholds, including:

-   **Connection utilization:** 70% of the applicable connection
    capacity.
-   **Idle connection age:** 240 minutes by default.
-   **Replication message freshness:** 30 seconds.
-   **Replication replay lag:** 30 seconds or 256 MB of WAL difference.

These thresholds are diagnostic indicators, not universal PostgreSQL
performance targets. Actual operating conditions, workload
characteristics, and application requirements should be considered when
evaluating the results.

## Reporting

`pg_smartcheck` supports console output and optional HTML reporting.

### Console Output

Each diagnostic check reports one of three primary statuses:

-   **OK:** No issue was detected by the check.
-   **WARNING:** A condition was detected that may require
    investigation.
-   **INFO:** Additional information is provided, or a check could not
    be performed.

Verbose mode (`-v`) displays additional diagnostic details for selected
checks, including bloat estimates and maintenance candidates.

Debug mode (`-b`) displays additional execution information useful when
troubleshooting the utility itself.

### HTML Reporting

The `--html` option generates a standalone HTML report containing:

-   Database host and database name.
-   PostgreSQL version and report generation timestamp.
-   Total number of checks.
-   Counts of OK, WARNING, and INFO results.
-   Detailed results for each health check.
-   Expandable sections containing additional diagnostic information.

The report is suitable for reviewing database health and sharing
diagnostic findings with other DBAs.

## Cron Setup Example

This example runs this tool four times per day at the :15 and :45 minute
marks. That gives runs at:

-   6:15 AM
-   12:45 PM
-   6:15 PM
-   10:45 PM

``` cron
15 6,18 * * * /usr/bin/python3 /path/to/pg_smartcheck.py -d mydb --html >> /var/log/pg_smartcheck_cron.log 2>&1
45 12,22 * * * /usr/bin/python3 /path/to/pg_smartcheck.py -d mydb --html >> /var/log/pg_smartcheck_cron.log 2>&1
```

Make sure the database authentication environment is available to the
scheduled process.

## Invocation

General syntax:

Minimum options using defaults:

`python pg_smartcheck.py -d <dbname> [connection options] [diagnostic options] [reporting options]`

The database name is required. Host, port, and user can be supplied
explicitly or obtained from the standard PostgreSQL/libpq environment
and configuration.

Make sure database authentication is configured before executing the
utility. The `PGPASSWORD` environment variable or a properly configured
`.pgpass` file can be used.

The following examples illustrate common ways to invoke the program.

`<br/>`{=html}`<br/>`{=html} **Basic health check**

Connect to a PostgreSQL database and execute the standard diagnostic
checks.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432`

`<br/>`{=html}`<br/>`{=html} **Generate an HTML report**

Execute the health checks and generate a detailed HTML report in the
current working directory.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 --html`

`<br/>`{=html}`<br/>`{=html} **Enable verbose output**

Display additional diagnostic information while performing the health
checks.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 -v`

`<br/>`{=html}`<br/>`{=html} **Monitor waits and long-running queries**

Report queries waiting on database resources for more than 30 seconds
and queries running longer than 10 minutes.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 -w 30 -l 10`

`<br/>`{=html}`<br/>`{=html} **Monitor idle sessions**

Report sessions idle in transaction for more than 5 minutes and idle
connections older than 120 minutes.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 -i 5 -o 120`

`<br/>`{=html}`<br/>`{=html} **Specify CPU capacity**

Provide the number of available CPUs for CPU-related diagnostic checks.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 -c 4`

`<br/>`{=html}`<br/>`{=html} **Run a comprehensive diagnostic report**

Combine activity thresholds, CPU capacity, verbose output, and HTML
reporting.

`python pg_smartcheck.py -h localhost -d testing -U postgres -p 5432 -w 30 -l 10 -i 5 -o 240 -c 4 -v --html`

`<br/>`{=html}`<br/>`{=html} **Capture console output in a log file**

Execute the utility and append console output to a log file.

`python3 pg_smartcheck.py 2>&1 | tee -a pg_smartcheck.log`

## Future Enhancements

Planned enhancements include additional monitoring capabilities and
integration with PgBouncer monitoring.
