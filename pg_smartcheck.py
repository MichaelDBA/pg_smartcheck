#!/usr/bin/env python3
###############################################################################
### COPYRIGHT NOTICE FOLLOWS.  DO NOT REMOVE
###############################################################################
### Copyright (c) 2026 SQLEXEC LLC
###
### Permission to use, copy, modify, and distribute this software and its
### documentation for any purpose, without fee, and without a written agreement
### is hereby granted, provided that the above copyright notice and this paragraph
### and the following two paragraphs appear in all copies.
###
### IN NO EVENT SHALL SQLEXEC LLC BE LIABLE TO ANY PARTY FOR DIRECT, INDIRECT,
### INDIRECT SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS,
### ARISING OUT OF THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF
### SQLEXEC LLC HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###
### SQLEXEC LLC SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT
### LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
### PARTICULAR PURPOSE. THE SOFTWARE PROVIDED HEREUNDER IS ON AN "AS IS" BASIS,
### AND SQLEXEC LLC HAS NO OBLIGATIONS TO PROVIDE MAINTENANCE, SUPPORT, UPDATES,
### ENHANCEMENTS, OR MODIFICATIONS.
###############################################################################
#
# Original Author: Michael Vitale, michaeldba@sqlexec.com
#
# Description: Comprehensive PostgreSQL database health analysis, performance diagnostics,
#              maintenance recommendations, and detailed reporting.
#
# Requirements:
#  1. PG 10 and up.
#  2. Python 3
#  3. psql client in the user's PATH
#  4. Password available through .pgpass, PGPASSWORD, or PostgreSQL authentication
#
# Execution:
#  The program can use the previous log file to evaluate elapsed time for certain checks.
#  Example:
#  python3 pg_smartcheck.py 2>&1 | tee -a pg_smartcheck.log
#
# TODOs:
#    1. Update pgbouncer_pkg and integrate its logic for PgBouncer monitoring.
#
# History:
# who did it         Date           did what
# ==========         =========      ==============================
# Michael Vitale     11/05/2026     Initial development, consolidating pg_report and pg_check.
# Michael Vitale     11/06/2026     Added more logic and refined it in context of BGWriter.
# Michael Vitale     11/07/2026     Added toast table analysis. Reuse connections.
###############################################################################

import sys
import os
import ipaddress
import time
import tempfile
import platform
import math
import subprocess
import shutil
import html
from datetime import datetime
from decimal import Decimal
from subprocess import Popen, PIPE
from optparse import OptionParser
import psycopg2

#############################################################################################
# GLOBALS
#############################################################################################
SUCCESS   = 0
ERROR     = -1
ERROR2    = -2
ERROR3    = -3
WARNING   = -4
DEFERRED  = 1
NOTICE    = 2
TOOLONG   = 3
HIGHLOAD  = 4

DESCRIPTION = "Comprehensive PostgreSQL database health analysis, performance diagnostics, maintenance recommendations, and detailed reporting."
VERSION     = 1.0
PROGNAME    = "pg_smartcheck"
ADATE       = "November 07, 2026"
PROGDATE    = "2026-11-07"
MARK_OK     = "[ OK ]  "
MARK_WARN   = "[WARN]  "
MARK_INFO   = "[INFO]  "
MARK_VERB   = "[VERB]  "

# Other defaults
DEFAULT_MAXCONNS_THRESHOLD_PCT = 70
DEFAULT_MAX_IDLE_AGE_MINS = 240
DEFAULT_REPLICATION_LAG_SECS = 30
DEFAULT_REPLICATION_LAG_BYTES = 256 * 1024 * 1024

# Alert notification identifiers retained from pg_check.py.
TESTALERT    = "TestAlert"
DIRSIZE      = "DirSize"
IDLECONNS    = "IdleConns"
WAITS        = "Waits"
IDLEINTRANS  = "IdleInTrans"
LONGQUERY    = "LongQuery"
ACTIVECONNS  = "ActiveConns"
LOAD1        = "Load1"
LOAD5        = "Load5"
LOAD15       = "Load15"
PGBOUNCER1   = "PGBouncer1"
PGBOUNCER2   = "PGBouncer2"
PGBOUNCER3   = "PGBouncer3"
PGBACKREST1  = "PGBackrest1"
REPLICATION  = "Replication"
PGHOSTUP     = "PGHostUp"

#############################################################################################
########################### CLASS DEFINITION ###############################################
#############################################################################################
class maint:
    def __init__(self):

        # Execution and connection settings.
        self.dbhost            = ""
        self.dbport            = "5432"
        self.dbuser            = ""
        self.database          = ""
        self.dbpass            = ""
        self.schema            = ""
        self.opsys             = ""
        self.connstring        = ""
        self.connection        = None
        self.local             = False
        self.connected         = False
        

        # Monitoring and alert options inherited from pg_check.py.
        self.waitslocks        = -1
        self.longquerymins     = -1
        self.idleintransmins   = -1
        self.idleconnmins      = -1
        self.vcpus             = -1
        self.verbose           = False
        self.debug             = False

        # Program and temporary-file settings.
        self.pid               = os.getpid()
        self.tempdir           = tempfile.gettempdir()
        self.tempfile          = ""
        self.pgbindir          = ""

        # PostgreSQL version and configuration.
        self.pgversionmajor    = Decimal("0.0")
        self.pgversionminor    = "0.0"
        self.pg_type           = "community"
        self.datadir           = ""
        self.waldir            = ""
        self.shared_buffers    = -1
        self.work_mem          = -1
        self.maint_work_mem    = -1
        self.shared_preload_libraries = ""


    ###########################################################
    def validate_parms(self):
        if self.database == "":
            return ERROR, "Database not provided."
        return SUCCESS, ""

    ###########################################################
    def convert_humanfriendly_to_MB(self, value):

        try:
            value = str(value).strip().lower()

            if not value:
                return -1

            # Separate the numeric value from its unit.
            number = ""
            unit = ""

            for char in value:
                if char.isdigit() or char in ".-":
                    number += char
                else:
                    unit += char

            amount = Decimal(number)
            unit = unit.strip()

            # Convert the value to megabytes.
            multipliers = {
                "b": Decimal(1) / Decimal(1024 * 1024),
                "kb": Decimal(1) / Decimal(1024),
                "k": Decimal(1) / Decimal(1024),
                "mb": Decimal(1),
                "m": Decimal(1),
                "gb": Decimal(1024),
                "g": Decimal(1024),
                "tb": Decimal(1024 * 1024),
                "t": Decimal(1024 * 1024),
            }

            # PostgreSQL settings without a displayed unit are treated as MB.
            multiplier = multipliers.get(unit, Decimal(1))

            return float(amount * multiplier)

        except Exception:
            return -1

    ###########################################################
    def executecmd(self, cmd, expect):

        if self.debug:
            print("[****] executecmd --> %s" % cmd)

        try:
            cmd_env = os.environ.copy()

            if self.dbpass:
                cmd_env["PGPASSWORD"] = self.dbpass

            if self.opsys == "posix":
                process = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, executable="/bin/bash", env=cmd_env)
            else:
                process = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, env=cmd_env)

            stdout, stderr = process.communicate()

        except (OSError, ValueError, subprocess.SubprocessError) as e:
            if self.verbose or self.debug:
                print("Command execution failed: %s" % e)

            return ERROR, str(e)

        values = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        err = stderr.decode("utf-8", errors="replace").strip() if stderr else ""

        rc = process.returncode

        if self.debug:
            print("[****] rc=%d stdout=***%s*** stderr=***%s***" % (rc, values, err))

        if rc != SUCCESS:
            return (ERROR2 if rc in (1, 2, 127) else rc), (err or values)

        if not values and expect:
            return ERROR3, "[ERROR] return set is empty"

        return SUCCESS, (values if values else err)
        

    ###########################################################
    def executesql(self, sql, expect=True):

        if not self.connection or self.connection.closed:
            return ERROR, "PostgreSQL connection is not available."

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)

                if cursor.description is None:
                    return SUCCESS, ""

                rows = cursor.fetchall()
                results = "\n".join("|".join("" if value is None else str(value) for value in row) for row in rows)

                if not results and expect:
                    return ERROR3, "[ERROR] return set is empty"

                return SUCCESS, results

        except psycopg2.Error as e:
            return ERROR2, str(e).strip()
            
            
    ###########################################################
    def get_configinfo(self):

        sql = "show all"
        cmd = "psql -X %s -At -c \"%s\" > \"%s\"" % (self.connstring, sql, self.tempfile)

        rc, results = self.executecmd(cmd, False)
        if rc != SUCCESS:
            errors = "Unable to get config info: %d %s\nsql=%s" % (rc, results, sql)
            return rc, errors

        try:
            with open(self.tempfile, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    aline = line.strip()

                    if not aline:
                        continue

                    fields = aline.split("|", 2)
                    if len(fields) < 2:
                        continue

                    name = fields[0].strip()
                    setting = fields[1].strip()

                    if name == "data_directory":
                        self.datadir = setting
                        self.waldir = os.path.join(self.datadir, "pg_wal" if self.pgversionmajor > Decimal("9.6") else "pg_xlog")

                        if "rdsdbdata" in self.datadir or self.datadir == "/database":
                            self.pg_type = "rds"

                    elif name == "log_directory":
                        self.logdir = setting

                    elif name == "max_connections":
                        self.max_connections = int(setting)

                    elif name == "shared_buffers":
                        self.shared_buffers = self.convert_humanfriendly_to_MB(setting)

                    elif name == "maintenance_work_mem":
                        self.maint_work_mem = self.convert_humanfriendly_to_MB(setting)

                    elif name == "work_mem":
                        self.work_mem = self.convert_humanfriendly_to_MB(setting)

                    elif name == "effective_cache_size":
                        self.eff_cache_size = self.convert_humanfriendly_to_MB(setting)

                    elif name == "shared_preload_libraries":
                        self.shared_preload_libraries = setting

                        if "rdsutils" in setting:
                            self.pg_type = "rds"

                    elif name == "rds.extensions":
                        self.pg_type = "rds"

        except (OSError, ValueError) as e:
            return ERROR, "Unable to parse PostgreSQL configuration: %s" % e

        return SUCCESS, "Configuration loaded."

    ###########################################################
    def get_pgversion(self):

        sql = "select trim(substring(version(), 12, position(' ' in substring(version(),12)))) || '-' || substring(foo.major from 12 for 3) as major from (select version() as major) foo"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to determine PostgreSQL version: %s" % results

        try:
            parsed = str(results).strip().split("-")

            if len(parsed) != 2:
                raise ValueError("Unexpected version string: %s" % results)

            self.pgversionminor = parsed[0]
            major = parsed[1].strip()

            self.pgversionmajor = Decimal(major if "." in major else major[:2])

        except Exception as e:
            return ERROR, "Unable to parse PostgreSQL version '%s': %s" % (results, e)

        return SUCCESS, str(results)

    ###########################################################
    def check_waitslocks(self):

        result = {
            "check": "waitslocks",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.waitslocks <= 0:
            result["message"] = "Wait/lock check disabled."
            return SUCCESS, result

        if self.pgversionmajor < Decimal("10.0"):
            result["status"] = "INFO"
            result["message"] = "Wait/lock check requires PostgreSQL 10 or newer."
            return SUCCESS, result

        sql1 = "select count(*) from pg_stat_activity where wait_event is NOT NULL AND wait_event <> 'DataFileRead' and state = 'active' and backend_type <> 'walsender' and now() - query_start > interval '%d seconds'" % self.waitslocks

        sql2 = "select 'db=' || datname || '  user=' || usename || '  appname=' || application_name || '  waitinfo=' || wait_event || '-' || wait_event_type || '  duration=' || " \
               "cast(EXTRACT(EPOCH FROM (now() - query_start)) as integer) || chr(10) || 'sql=' || regexp_replace(replace(regexp_replace(query, E'[\\n\\r]+', ' ', 'g' ),'    ',''), " \
               "'[^\\x20-\\x7f\\x0d\\x1b]', '', 'g') || chr(10) from pg_stat_activity where wait_event IS NOT NULL and wait_event <> 'DataFileRead' and state = 'active' AND " \
               "backend_type <> 'walsender' and now() - query_start > interval '%d seconds'" % self.waitslocks                

        sql3 = "SELECT 'blocked_pid=' || a.pid || chr(10) || 'blocked_query=' || coalesce(regexp_replace(a.query, E'[\\n\\r]+', ' ', 'g'), 'N/A') || " \
               "chr(10) || 'blockers=' || coalesce((SELECT string_agg('pid=' || b.pid || ' user=' || coalesce(b.usename, 'N/A') || ' query=' || coalesce(regexp_replace(b.query, E'[\\n\\r]+', ' ', 'g'), 'N/A'), '; ') " \
               "FROM unnest(pg_catalog.pg_blocking_pids(a.pid)) AS p(pid) JOIN pg_catalog.pg_stat_activity b ON b.pid = p.pid), 'none') || " \
               "chr(10) || 'blockees=' || coalesce((SELECT string_agg('pid=' || x.pid || ' user=' || coalesce(x.usename, 'N/A') || ' query=' || coalesce(regexp_replace(x.query, E'[\\n\\r]+', ' ', 'g'), 'N/A'), '; ') " \
               "FROM pg_catalog.pg_stat_activity x WHERE a.pid = ANY(pg_catalog.pg_blocking_pids(x.pid))), 'none') || chr(10) || chr(10) " \
               "FROM pg_catalog.pg_stat_activity a WHERE a.wait_event IS NOT NULL AND a.wait_event <> 'DataFileRead' " \
               "AND a.state = 'active' AND a.backend_type <> 'walsender' " \
               "AND now() - a.query_start > interval '%d seconds'" % self.waitslocks               

        rc, results = self.executesql(sql1, True)
        if rc != SUCCESS:
            return rc, "Unable to get count of waiting queries: %s" % results
            
        try:
            blocked_queries_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid wait/lock count returned: %s" % results

        result["count"] = blocked_queries_cnt

        if blocked_queries_cnt == 0:
            result["message"] = "No waiting/blocked queries longer than %d seconds were detected." % self.waitslocks
            return SUCCESS, result

        result["status"] = "WARNING"
        result["message"] = "%d waiting/blocked queries longer than %d seconds were detected." % (blocked_queries_cnt, self.waitslocks)

        rc, results2 = self.executesql(sql2, False)
        if rc != SUCCESS:
            results2 = ""
            
        rc, results3 = self.executesql(sql3, False)
        if rc != SUCCESS:
            results3 = ""
            
        result["details"] = (results2 or "") + "\n" + (results3 or "")

        if not result["details"].strip():
            result["message"] += " Details are no longer available."

        return SUCCESS, result


    ###########################################################
    def check_idleintrans(self):

        result = {
            "check": "idleintrans",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.idleintransmins <= 0:
            result["message"] = "Idle-in-transaction check disabled."
            return SUCCESS, result

        sql1 = "select count(*) from pg_stat_activity where state = 'idle in transaction' and now() - state_change > interval '%d minutes'" % self.idleintransmins

        sql2 = "select 'pid=' || pid || '  db=' || coalesce(datname, 'N/A') || '  user=' || coalesce(usename, 'N/A') || '  app=' || coalesce(application_name, 'N/A') || '  clientip=' || coalesce(client_addr::text, 'N/A') || '  duration=' || round(EXTRACT(EPOCH FROM (now() - state_change)) / 60)::integer || ' mins' from pg_stat_activity where state = 'idle in transaction' and now() - state_change > interval '%d minutes'" % self.idleintransmins

        rc, results = self.executesql(sql1, True)
        if rc != SUCCESS:
            return rc, "Unable to get count of idle-in-transaction connections: %s" % results
            
        try:
            idle_intrans_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid idle-in-transaction count returned: %s" % results

        result["count"] = idle_intrans_cnt

        if idle_intrans_cnt == 0:
            result["message"] = "No \"idle in transaction\" sessions longer than %d minute(s) were detected." % self.idleintransmins
            return SUCCESS, result

        result["status"] = "WARNING"
        result["message"] = "%d \"idle in transaction\" sessions longer than %d minute(s) were detected." % (idle_intrans_cnt, self.idleintransmins)

        rc, details = self.executesql(sql2, False)
        if rc == SUCCESS:
            result["details"] = details or ""
            
        if not result["details"].strip():
            result["message"] += " Details are no longer available."

        return SUCCESS, result


    ###########################################################
    def check_idleconns(self):

        result = {
            "check": "idleconns",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.idleconnmins <= 0:
            result["message"] = "Idle connection check disabled."
            return SUCCESS, result

        sql1 = "select count(*) from pg_stat_activity where state = 'idle' and now() - state_change > interval '%d minutes'" % self.idleconnmins

        sql2 = "select 'pid=' || pid || '  db=' || coalesce(datname, 'N/A') || '  user=' || coalesce(usename, 'N/A') || '  app=' || coalesce(application_name, 'N/A') || '  clientip=' || coalesce(client_addr::text, 'N/A') || '  idle=' || round(EXTRACT(EPOCH FROM (now() - state_change)) / 60)::integer || ' mins' from pg_stat_activity where state = 'idle' and now() - state_change > interval '%d minutes'" % self.idleconnmins

        rc, results = self.executesql(sql1, True)
        if rc != SUCCESS:
            return rc, "Unable to get count of idle connections: %s" % results
            
        try:
            idle_connections_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid idle connection count returned: %s" % results

        result["count"] = idle_connections_cnt

        if idle_connections_cnt == 0:
            result["message"] = "No idle connections older than %d minute(s) were detected." % self.idleconnmins
            return SUCCESS, result

        result["status"] = "WARNING"
        result["message"] = "%d idle connections older than %d minute(s) were detected." % (idle_connections_cnt, self.idleconnmins)

        rc, details = self.executesql(sql2, False)
        if rc == SUCCESS:
            result["details"] = details or ""
            
        if not result["details"].strip():
            result["message"] += " Details are no longer available."

        return SUCCESS, result

    ###########################################################
    def check_longquery(self):

        result = {
            "check": "longquery",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.longquerymins <= 0:
            result["message"] = "Long-running query check disabled."
            return SUCCESS, result

        if self.pgversionmajor < Decimal("10.0"):
            return ERROR, "Long-running query check currently requires PostgreSQL 10 or newer."

        sql1 = "select count(*) from pg_stat_activity where backend_type <> 'walsender' and state not ilike 'idle%%' and query <> '' and now() - query_start > interval '%d minutes'" % self.longquerymins

        sql2 = "select 'pid=' || pid || '  db=' || coalesce(datname, 'N/A') || '  user=' || coalesce(usename, 'N/A') || '  app=' || coalesce(application_name, 'N/A') || '  minutes=' || cast(EXTRACT(EPOCH FROM (now() - query_start)) as integer) / 60 || E'\\n' || 'sql=' || regexp_replace(replace(regexp_replace(query, E'[\\n\\r]+', ' ', 'g'), '    ', ''), '[^\\x20-\\x7f\\x0d\\x1b]', '', 'g') || E'\\n\\n' from pg_stat_activity where backend_type <> 'walsender' and state not ilike 'idle%%' and query <> '' and now() - query_start > interval '%d minutes'" % self.longquerymins

        rc, results = self.executesql(sql1, True)
        if rc != SUCCESS:
            return rc, "Unable to get count of long-running queries: %s" % results
            
        try:
            long_queries_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid long-running query count returned: %s" % results

        result["count"] = long_queries_cnt

        if long_queries_cnt == 0:
            result["message"] = "No long-running queries longer than %d minute(s) were detected." % self.longquerymins
            return SUCCESS, result

        result["status"] = "WARNING"
        result["message"] = "%d long-running queries longer than %d minute(s) were detected." % (long_queries_cnt, self.longquerymins)

        rc, details = self.executesql(sql2, False)
        if rc != SUCCESS:
            return rc, "Unable to get details for long-running queries: %s" % details
            
        result["details"] = (details or "").rstrip()

        if not result["details"].strip():
            result["message"] += " Details are no longer available."

        return SUCCESS, result


    ###########################################################
    def check_load(self):

        result = {
            "check": "load",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if not self.local:
            result["status"] = "INFO"
            result["message"] = "OS load check not applicable for remote DB instances."
            return SUCCESS, result

        if self.opsys != "posix":
            result["status"] = "INFO"
            result["message"] = "OS load averages are not available on this platform."
            return SUCCESS, result

        vcpus = self.vcpus if self.vcpus > 0 else os.cpu_count()

        if not vcpus:
            return ERROR, "Unable to determine the number of vCPUs for the OS load check."

        cmd = "uptime"
        rc, results = self.executecmd(cmd, True)

        if rc != SUCCESS:
            return rc, "Unable to get OS load averages: %s" % results

        try:
            output = results.lower()
            load_index = output.find("load average:")

            if load_index == -1:
                load_index = output.find("load averages:")

            if load_index == -1:
                raise ValueError("Load average information not found.")

            load_values = output[load_index:].split(":", 1)[1].strip().split(",")

            if len(load_values) < 3:
                raise ValueError("Expected three load average values.")

            load1 = Decimal(load_values[0].strip())
            load5 = Decimal(load_values[1].strip())
            load15 = Decimal(load_values[2].strip())

        except (ValueError, ArithmeticError) as e:
            return ERROR, "Unable to parse OS load averages: %s" % e

        threshold = Decimal("0.90") * vcpus

        result["count"] = 1 if max(load1, load5, load15) > threshold else 0

        if result["count"] > 0:
            result["status"] = "WARNING"
            result["message"] = "OS load averages exceed 90%% of CPU capacity: 1 min=%.2f  5 min=%.2f  15 min=%.2f  vCPUs=%d" % (load1, load5, load15, vcpus)
        else:
            result["message"] = "OS load averages: 1 min=%.2f  5 min=%.2f  15 min=%.2f  vCPUs=%d" % (load1, load5, load15, vcpus)

        return SUCCESS, result        


    ###########################################################
    def check_activeconns(self):

        result = {
            "check": "activeconns",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        detected_vcpus = None

        if self.local:
            detected_vcpus = os.cpu_count()

            if self.vcpus > 0:
                vcpus = self.vcpus

                if detected_vcpus and self.vcpus != detected_vcpus:
                    print(MARK_INFO + "User-specified vCPU count (%d) differs from detected CPU count (%d). Using %d." % (self.vcpus, detected_vcpus, self.vcpus))

            else:
                vcpus = detected_vcpus

        else:
            if self.vcpus <= 0:
                result["status"] = "INFO"
                result["message"] = "Active connection load check not possible for remote DB instances without a specified vCPU count."
                return SUCCESS, result

            vcpus = self.vcpus

        if not vcpus:
            return ERROR, "Unable to determine the number of vCPUs for the active connection check."

        threshold = Decimal(str(vcpus)) * Decimal("2.5")

        sql = "select count(*) from pg_stat_activity WHERE backend_type = 'client backend' AND state in ('active', 'idle in transaction')"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get active connection count: %s" % results

        try:
            active_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid active connection count returned: %s" % results

        result["count"] = active_cnt

        if Decimal(active_cnt) > threshold:
            result["status"] = "WARNING"
            result["message"] = "Active connection load exceeds threshold: active_sessions=%d  threshold=%.1f  vCPUs=%d" % (active_cnt, threshold, vcpus)
        else:
            result["message"] = "Active connection load is within threshold: active_sessions=%d  threshold=%.1f  vCPUs=%d" % (active_cnt, threshold, vcpus)

        return SUCCESS, result
        

    ###########################################################
    def check_maxconns(self):

        result = {
            "check": "maxconns",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.pgversionmajor < Decimal("10.0"):
            return ERROR, "Maximum connection check currently requires PostgreSQL 10 or newer."

        if self.pgversionmajor >= Decimal("16.0"):
            sql = "select (select setting::integer from pg_settings where name = 'max_connections'), coalesce((select setting::integer from pg_settings where name = 'superuser_reserved_connections'), 0), "\
                  "coalesce((select setting::integer from pg_settings where name = 'reserved_connections'), 0), (select count(*) from pg_stat_activity where backend_type = 'client backend')"
        else:
            sql = "select (select setting::integer from pg_settings where name = 'max_connections'), coalesce((select setting::integer from pg_settings where name = 'superuser_reserved_connections'), 0), 0,  "\
                  "(select count(*) from pg_stat_activity where backend_type = 'client backend')"        

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get PostgreSQL connection utilization: %s" % results
            
        try:
            values = results.strip().split("|")

            if len(values) != 4:
                raise ValueError("Expected four values.")

            max_connections = int(values[0])
            superuser_reserved = int(values[1])
            reserved_connections = int(values[2])
            client_connections = int(values[3])

        except ValueError:
            return ERROR, "Invalid PostgreSQL connection information returned: %s" % results

        usable_capacity = max_connections - superuser_reserved - reserved_connections

        if usable_capacity <= 0:
            return ERROR, "Invalid usable connection capacity: %d" % usable_capacity

        utilization = Decimal(client_connections) / Decimal(usable_capacity) * Decimal("100")

        result["count"] = client_connections

        if utilization >= Decimal(str(DEFAULT_MAXCONNS_THRESHOLD_PCT)):
            result["status"] = "WARNING"
            result["message"] = "PostgreSQL connection utilization approaching capacity: %d/%d (%.1f%%)  max_connections=%d" % (client_connections, usable_capacity, utilization, max_connections)
        else:
            result["message"] = "PostgreSQL connection utilization: %d/%d (%.1f%%)  max_connections=%d" % (client_connections, usable_capacity, utilization, max_connections)

        return SUCCESS, result
        

    ###########################################################
    def check_dbconflicts(self):

        result = {
            "check": "dbconflicts",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.pgversionmajor < Decimal("9.1"):
            result["status"] = "INFO"
            result["message"] = "Database conflict check requires PostgreSQL 9.1 or newer."
            return SUCCESS, result

        if self.pgversionmajor < Decimal("9.2"):
            sql = "select conflicts from pg_stat_database where datname = current_database()"
        else:
            sql = "select conflicts, deadlocks, temp_files, temp_bytes, coalesce(stats_reset::text, 'N/A') from pg_stat_database where datname = current_database()"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get database conflict statistics: %s" % results
            
        try:
            values = results.strip().split("|")

            if self.pgversionmajor < Decimal("9.2"):
                if len(values) != 1:
                    raise ValueError("Expected one value.")

                conflicts = int(values[0])
                deadlocks = 0
                temp_files = 0
                temp_bytes = 0
                stats_reset = "N/A"
            else:
                if len(values) != 5:
                    raise ValueError("Expected five values.")

                conflicts = int(values[0])
                deadlocks = int(values[1])
                temp_files = int(values[2])
                temp_bytes = int(values[3])
                stats_reset = values[4]

        except ValueError:
            return ERROR, "Invalid database conflict statistics returned: %s" % results

        result["count"] = conflicts + deadlocks + temp_files

        temp_size = float(temp_bytes)
        temp_units = ["B", "KB", "MB", "GB", "TB"]
        temp_unit = temp_units[0]

        for unit in temp_units[1:]:
            if temp_size < 1024:
                break
            temp_size /= 1024
            temp_unit = unit

        temp_bytes_display = "%.2f %s" % (temp_size, temp_unit)

        if conflicts > 0 or deadlocks > 0 or (conflicts == 0 and deadlocks == 0 and temp_bytes > 1073741824):
            result["status"] = "WARNING"
            result["message"] = "Database activity requires investigation: conflicts=%d  deadlocks=%d  temp_files=%d  temp_bytes=%s  stats_reset=%s" % (conflicts, deadlocks, temp_files, temp_bytes_display, stats_reset)
        else:
            result["message"] = "No database conflicts or excessive temporary file usage found."            

        return SUCCESS, result
        
        
    ###########################################################
    def check_cachehit(self):

        result = {
            "check": "cachehit",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "select blks_read, blks_hit, round((blks_hit::float/(blks_read+blks_hit+1)*100)::numeric, 2) as cachehitratio from pg_stat_database where datname = current_database()"        

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get database cache hit ratio: %s" % results
            
        try:
            cols = results.strip().split("|")

            if len(cols) != 3:
                raise ValueError("Expected three values.")

            blks_read = int(cols[0].strip())
            blks_hit = int(cols[1].strip())
            cache_ratio = Decimal(cols[2].strip())

        except (ValueError, IndexError):
            return ERROR, "Invalid database cache hit information returned: %s" % results

        if cache_ratio < Decimal("90.0"):
            result["status"] = "WARNING"
            result["count"] = 1
            result["message"] = "Low cache hit ratio: %.2f%% (blocks hit vs blocks read)  blocks_read=%d  blocks_hit=%d" % (cache_ratio, blks_read, blks_hit)
        else:
            result["message"] = "High cache hit ratio: %.2f%% (blocks hit vs blocks read)  blocks_read=%d  blocks_hit=%d" % (cache_ratio, blks_read, blks_hit)
            
        return SUCCESS, result


    ###########################################################
    def check_checkpoints(self):

        result = {
            "check": "checkpoints",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.pgversionmajor < Decimal("9.5"):
            result["status"] = "INFO"
            result["message"] = "Checkpoint monitoring requires PostgreSQL 9.5 or newer."
            return SUCCESS, result

        # Get checkpoint statistics.

        if self.pgversionmajor >= Decimal("17.0"):
            sql = "select num_timed, num_requested, coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0), write_time, sync_time from pg_stat_checkpointer"
        else:
            sql = "select checkpoints_timed, checkpoints_req, coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0), checkpoint_write_time, checkpoint_sync_time from pg_stat_bgwriter"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get checkpoint statistics: %s" % results
            
        try:
            values = results.strip().split("|")

            if len(values) != 5:
                raise ValueError("Expected five values.")

            checkpoints_timed = int(values[0])
            checkpoints_req = int(values[1])
            elapsed_secs = float(values[2])
            checkpoint_write_time = float(values[3])
            checkpoint_sync_time = float(values[4])

        except (ValueError, TypeError):
            return ERROR, "Invalid checkpoint statistics returned: %s" % results

        # Get checkpoint configuration.

        sql = "select current_setting('checkpoint_timeout'), current_setting('max_wal_size'), pg_size_bytes(current_setting('max_wal_size')), current_setting('checkpoint_completion_target')"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get checkpoint configuration: %s" % results
            
        try:
            values = results.strip().split("|")

            if len(values) != 4:
                raise ValueError("Expected four values.")

            checkpoint_timeout = values[0]
            max_wal_size = values[1]
            max_wal_size_bytes = int(values[2])
            completion_target = float(values[3])

            timeout_value = checkpoint_timeout.lower().strip()
            timeout_number = float("".join(c for c in timeout_value if c.isdigit() or c == "."))

            if timeout_value.endswith("ms"):
                timeout_secs = timeout_number / 1000
            elif timeout_value.endswith("min"):
                timeout_secs = timeout_number * 60
            elif timeout_value.endswith("h"):
                timeout_secs = timeout_number * 3600
            elif timeout_value.endswith("d"):
                timeout_secs = timeout_number * 86400                
            elif timeout_value.endswith("s"):
                timeout_secs = timeout_number
            else:
                raise ValueError("Unsupported checkpoint_timeout unit: %s" % checkpoint_timeout)

        except (ValueError, TypeError):
            return ERROR, "Invalid checkpoint configuration returned: %s" % results

        # Calculate checkpoint statistics.
        total_checkpoints = checkpoints_timed + checkpoints_req
        result["count"] = total_checkpoints

        avg_interval_secs = elapsed_secs / total_checkpoints if total_checkpoints > 0 else elapsed_secs
        avg_interval_mins = avg_interval_secs / 60.0

        avg_write_secs = checkpoint_write_time / 1000.0 / total_checkpoints if total_checkpoints > 0 else 0.0
        avg_sync_secs = checkpoint_sync_time / 1000.0 / total_checkpoints if total_checkpoints > 0 else 0.0

        timed_pct = checkpoints_timed / total_checkpoints * 100.0 if total_checkpoints > 0 else 0.0
        requested_pct = checkpoints_req / total_checkpoints * 100.0 if total_checkpoints > 0 else 0.0

        expected_write_secs = avg_interval_secs * completion_target
        pacing_pct = avg_write_secs / expected_write_secs * 100.0 if expected_write_secs > 0 else 0.0

         # Get WAL generation statistics where supported.
        wal_mb_per_hour = None
        if self.pgversionmajor >= Decimal("14.0"):
            sql = "select coalesce(wal_bytes, 0), coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0) from pg_stat_wal"
            rc, results = self.executesql(sql, True)
            if rc == SUCCESS:
                try:
                    values = results.strip().split("|")

                    if len(values) != 2:
                        raise ValueError("Expected two values.")

                    wal_bytes = int(values[0])
                    wal_elapsed_secs = float(values[1])

                    if wal_elapsed_secs > 0:
                        wal_mb_per_hour = wal_bytes / 1024.0 / 1024.0 / (wal_elapsed_secs / 3600.0)

                except (ValueError, TypeError):
                    wal_mb_per_hour = None

        # Get a snapshot of checkpointer and background writer wait events.
        wait_events = {}

        if self.pgversionmajor >= Decimal("10.0"):

            sql = "select backend_type, coalesce(wait_event_type, 'N/A'), coalesce(wait_event, 'N/A') from pg_stat_activity where backend_type in ('checkpointer', 'background writer') order by backend_type"

            rc, results = self.executesql(sql, True)
            if rc == SUCCESS:
                for line in results.strip().splitlines():
                    values = line.split("|")

                    if len(values) == 3:
                        wait_events[values[0]] = values[2]

        # Evaluate checkpoint configuration and activity.
        warnings = []

        if timeout_secs < 120:
            warnings.append("checkpoint_timeout is below 2 minutes")
        elif timeout_secs > 1800:
            warnings.append("checkpoint_timeout exceeds 30 minutes")

        if total_checkpoints > 0:
            if avg_interval_secs < 120:
                warnings.append("checkpoints are occurring too frequently")
            elif avg_interval_secs >= 1800:
                warnings.append("checkpoints are occurring too far apart")

        # Build interpretation-focused diagnostic messages.

        details = []

        # Interpret checkpoint activity.

        if total_checkpoints > 0:

            if requested_pct < 10.0:
                details.append("Checkpoint cadence is predominantly timeout-driven. WAL pressure does not appear to be forcing frequent checkpoints. timed=%.1f%% requested=%.1f%% (requested_pct < 10.0)" % (timed_pct, requested_pct))
            else:
                details.append("Requested checkpoints represent a significant portion of activity. Investigate WAL generation and max_wal_size. timed=%.1f%% requested=%.1f%% (requested_pct >= 10.0)" % (timed_pct, requested_pct))

            if pacing_pct >= 80.0:
                details.append("Checkpoint writes appear well paced across the configured completion window. Average write time=%.2f secs expected window=%.2f secs completion_target=%.2f (pacing_pct >= 80.0)" % (avg_write_secs, expected_write_secs, completion_target))
            elif pacing_pct > 0:
                details.append("Checkpoint write activity is below the expected completion window. Checkpoints may be completing sooner than the configured target. Average write time=%.2f secs expected window=%.2f secs pacing=%.1f%% (0 < pacing_pct < 80.0)" % (avg_write_secs, expected_write_secs, pacing_pct))
            else:
                details.append("Checkpoint write pacing could not be evaluated. (pacing_pct <= 0)")

            if avg_sync_secs < 1.0:
                details.append("Checkpoint synchronization time is very low. Negligible time is being spent waiting for checkpoint file synchronization. sync=%.2f secs (avg_sync_secs < 1.0)" % avg_sync_secs)
            else:
                details.append("Checkpoint synchronization time is elevated. Investigate storage latency and checkpoint I/O performance. sync=%.2f secs (avg_sync_secs >= 1.0)" % avg_sync_secs)

        else:
            details.append("Checkpoint activity could not be evaluated because no checkpoints have been recorded since statistics reset. (total_checkpoints == 0)")

        # Interpret WAL generation.

        if wal_mb_per_hour is not None:

            wal_mb_per_timeout = wal_mb_per_hour * timeout_secs / 3600.0
            wal_limit_mb = max_wal_size_bytes / 1024.0 / 1024.0

            if wal_limit_mb <= 0:
                details.append("WAL generation could not be compared with max_wal_size because the configured limit is invalid. (wal_limit_mb <= 0)")
            else:
                wal_pct = wal_mb_per_timeout / wal_limit_mb * 100.0

                if wal_pct < 50.0:
                    details.append("WAL generation is comfortably below max_wal_size. Timeout-driven checkpoints are much more likely than WAL-size-driven checkpoints. WAL=%.2f MB per timeout max_wal_size=%.0f MB (wal_pct < 50.0)" \
                                   % (wal_mb_per_timeout, wal_limit_mb))
                elif wal_pct < 100.0:
                    details.append("WAL generation is approaching max_wal_size. Monitor requested checkpoints for signs of WAL-driven activity. WAL=%.2f MB per timeout max_wal_size=%.0f MB (50.0 <= wal_pct < 100.0)" \
                                   % (wal_mb_per_timeout, wal_limit_mb))
                else:
                    details.append("WAL generation exceeds max_wal_size per checkpoint timeout. WAL pressure may be triggering additional checkpoints. WAL=%.2f MB per timeout max_wal_size=%.0f MB (wal_pct >= 100.0)" \
                                   % (wal_mb_per_timeout, wal_limit_mb))

        else:
            details.append("WAL generation statistics are unavailable or could not be calculated. (wal_mb_per_hour is None)")

        # Interpret background writer and checkpointer activity.

        background_wait = wait_events.get("background writer", "N/A")
        checkpointer_wait = wait_events.get("checkpointer", "N/A")

        if background_wait == "BgWriterHibernate":
            details.append("Background writer is sleeping normally. wait_event=%s (background_wait == 'BgWriterHibernate')" % background_wait)
        elif background_wait == "N/A":
            details.append("Background writer wait status is unavailable. (background_wait == 'N/A')")
        else:
            details.append("Background writer is active or waiting. Current wait event=%s (background_wait != 'BgWriterHibernate' and background_wait != 'N/A')" % background_wait)

        if checkpointer_wait == "CheckpointWriteDelay":
            details.append("Checkpointer is waiting between writes as part of checkpoint pacing. wait_event=%s (checkpointer_wait == 'CheckpointWriteDelay')" % checkpointer_wait)
        elif checkpointer_wait == "N/A":
            details.append("Checkpointer wait status is unavailable. (checkpointer_wait == 'N/A')")
        else:
            details.append("Checkpointer is in its processing loop. Current wait event=%s (checkpointer_wait != 'CheckpointWriteDelay' and checkpointer_wait != 'N/A')" % checkpointer_wait)

        # Set the overall checkpoint status and summary message.
        if warnings:
            result["status"] = "WARNING"
            result["message"] = "Checkpoint warning: %s. interval=%.2f mins timed=%.1f%% requested=%.1f%%" % (", ".join(warnings), avg_interval_mins, timed_pct, requested_pct)
        elif total_checkpoints == 0:
            result["status"] = "INFO"
            result["message"] = "No checkpoint activity recorded since statistics reset."
        else:
            result["message"] = "Checkpoint activity is healthy. Checkpoints are occurring at the expected interval without excessive WAL-driven activity."

        result["details"] = "\n".join(details)

        return SUCCESS, result


    ###########################################################
    def check_bgwriter(self):

        result = {
            "check": "bgwriter",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        buffers_backend_fsync = None
        block_size = 0
        maxwritten_clean = 0
        maxwritten_elapsed_secs = 0

        # PostgreSQL 15: Use cumulative buffer counters.
        if self.pgversionmajor == Decimal("15.0"):
            sql = "select buffers_clean, buffers_backend, buffers_backend_fsync, maxwritten_clean, buffers_checkpoint, coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0), current_setting('block_size')::bigint from pg_stat_bgwriter"
            rc, results = self.executesql(sql, True)
            if rc != SUCCESS:
                return rc, "Unable to get background writer statistics: %s" % results

            try:
                values = results.strip().split("|")

                if len(values) != 7:
                    raise ValueError("Expected seven values.")

                buffers_clean = int(values[0])
                buffers_backend = int(values[1])
                buffers_backend_fsync = int(values[2])
                maxwritten_clean = int(values[3])
                buffers_checkpoint = int(values[4])
                elapsed_secs = float(values[5])
                block_size = int(values[6])

                if elapsed_secs <= 0 or block_size <= 0:
                    raise ValueError("Invalid statistics elapsed time or block size.")

                bgwriter_write_rate = buffers_clean / elapsed_secs
                backend_write_rate = buffers_backend / elapsed_secs
                checkpoint_write_rate = buffers_checkpoint / elapsed_secs
                maxwritten_elapsed_secs = elapsed_secs

            except (ValueError, TypeError):
                return ERROR, "Invalid background writer statistics returned: %s" % results

        # PostgreSQL 18: Use pg_stat_io and pg_stat_checkpointer.
        elif self.pgversionmajor == Decimal("18.0"):

            sql = "select io.backend_bytes, io.elapsed_secs, bg.buffers_clean, bg.maxwritten_clean, bg.elapsed_secs, cp.buffers_written, cp.elapsed_secs, current_setting('block_size')::bigint " \
                  "from (select coalesce(sum(write_bytes), 0) as backend_bytes, coalesce(extract(epoch from (clock_timestamp() - min(stats_reset))), 0) as elapsed_secs " \
                  "from pg_stat_io where backend_type = 'client backend' and object = 'relation' and context = 'normal') io " \
                  "cross join (select buffers_clean, maxwritten_clean, coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0) as elapsed_secs from pg_stat_bgwriter) bg " \
                  "cross join (select buffers_written, coalesce(extract(epoch from (clock_timestamp() - stats_reset)), 0) as elapsed_secs from pg_stat_checkpointer) cp"

            rc, results = self.executesql(sql, True)
            if rc != SUCCESS:
                return rc, "Unable to get background writer and I/O statistics: %s" % results

            try:
                values = results.strip().split("|")

                if len(values) != 8:
                    raise ValueError("Expected eight values.")

                backend_bytes = int(values[0])
                backend_elapsed_secs = float(values[1])
                buffers_clean = int(values[2])
                maxwritten_clean = int(values[3])
                bgwriter_elapsed_secs = float(values[4])
                buffers_checkpoint = int(values[5])
                checkpoint_elapsed_secs = float(values[6])
                block_size = int(values[7])

                if backend_elapsed_secs <= 0 or bgwriter_elapsed_secs <= 0 or checkpoint_elapsed_secs <= 0 or block_size <= 0:
                    raise ValueError("Invalid statistics elapsed time or block size.")

                backend_write_rate = backend_bytes / backend_elapsed_secs / block_size
                bgwriter_write_rate = buffers_clean / bgwriter_elapsed_secs
                checkpoint_write_rate = buffers_checkpoint / checkpoint_elapsed_secs
                maxwritten_elapsed_secs = bgwriter_elapsed_secs

            except (ValueError, TypeError):
                return ERROR, "Invalid background writer and I/O statistics returned: %s" % results
        else:
            result["status"] = "INFO"
            result["message"] = "Background writer monitoring currently supports PostgreSQL 15 and 18."
            return SUCCESS, result

        # Calculate write percentages and background writer limit rate.
        total_write_rate = backend_write_rate + bgwriter_write_rate + checkpoint_write_rate

        backend_write_pct = backend_write_rate / total_write_rate * 100.0 if total_write_rate > 0 else 0.0
        bgwriter_write_pct = bgwriter_write_rate / total_write_rate * 100.0 if total_write_rate > 0 else 0.0
        checkpoint_write_pct = checkpoint_write_rate / total_write_rate * 100.0 if total_write_rate > 0 else 0.0

        maxwritten_rate = maxwritten_clean / maxwritten_elapsed_secs * 60.0 if maxwritten_elapsed_secs > 0 else 0.0

        # Get background writer configuration.
        sql = "select current_setting('bgwriter_lru_multiplier'), current_setting('bgwriter_lru_maxpages'), current_setting('bgwriter_delay'), current_setting('shared_buffers')"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get background writer configuration: %s" % results
        try:
            values = results.strip().split("|")

            if len(values) != 4:
                raise ValueError("Expected four values.")

            bgwriter_lru_multiplier = values[0]
            bgwriter_lru_maxpages = values[1]
            bgwriter_delay = values[2]
            shared_buffers = values[3]

        except (ValueError, TypeError):
            return ERROR, "Invalid background writer configuration returned: %s" % results

        # Calculate background writer cleaning capacity.
        bgwriter_capacity_pct = 0.0
        bgwriter_maxpages = int(bgwriter_lru_maxpages)
        bgwriter_delay_secs = float(bgwriter_delay.replace("ms", "")) / 1000.0

        if bgwriter_maxpages > 0 and bgwriter_delay_secs > 0:
            bgwriter_capacity = bgwriter_maxpages / bgwriter_delay_secs
            bgwriter_capacity_pct = bgwriter_write_rate / bgwriter_capacity * 100.0

        # Evaluate background writer activity.
        warnings = []

        if buffers_backend_fsync is not None and buffers_backend_fsync > 0:
            warnings.append("backend processes have performed fsync operations because the background writer fsync request queue was full (buffers_backend_fsync=%d)" % buffers_backend_fsync)

        if backend_write_pct > (bgwriter_write_pct + checkpoint_write_pct):
            warnings.append("client backends are performing more writes than the background writer and checkpointer combined (backend_write_pct=%.1f%%)" % backend_write_pct)

        if maxwritten_rate >= 5.0 and bgwriter_capacity_pct >= 50.0:
            warnings.append("background writer is frequently reaching its cleaning limit while operating near its capacity (maxwritten_clean=%.2f/min, capacity_utilization=%.1f%%)" % (maxwritten_rate, bgwriter_capacity_pct))
        elif maxwritten_rate >= 1.0 and bgwriter_capacity_pct >= 10.0:
            warnings.append("background writer is repeatedly reaching its cleaning limit while using substantial cleaning capacity (maxwritten_clean=%.2f/min, capacity_utilization=%.1f%%)" % (maxwritten_rate, bgwriter_capacity_pct))            

        # Build diagnostic details.
        details = []

        details.append("Backend writes=%.2f buffers/sec (%.1f%%), background writer writes=%.2f buffers/sec (%.1f%%), checkpoint writes=%.2f buffers/sec (%.1f%%)" \
                       % (backend_write_rate, backend_write_pct, bgwriter_write_rate, bgwriter_write_pct, checkpoint_write_rate, checkpoint_write_pct))

        details.append("Background writer limit hits=%.2f/min (maxwritten_clean=%d), cleaning capacity utilization=%.2f%% (theoretical maximum=%.2f buffers/sec)" \
                       % (maxwritten_rate, maxwritten_clean, bgwriter_capacity_pct, bgwriter_maxpages / bgwriter_delay_secs if bgwriter_maxpages > 0 and bgwriter_delay_secs > 0 else 0.0))

        if buffers_backend_fsync is None:
            details.append("Backend fsync queue-full statistics are unavailable on PostgreSQL 17+.")
        else:
            details.append("Backend fsync queue-full events=%d" % buffers_backend_fsync)

        details.append("Background writer configuration: bgwriter_lru_multiplier=%s bgwriter_lru_maxpages=%s bgwriter_delay=%s shared_buffers=%s" \
                       % (bgwriter_lru_multiplier, bgwriter_lru_maxpages, bgwriter_delay, shared_buffers))

        result["count"] = len(warnings)
        result["details"] = "\n".join(details)

        if warnings:
            result["status"] = "WARNING"
            result["message"] = "Background writer activity needs attention: %s." % "; ".join(warnings)
        else:
            result["message"] = "Background writer activity is healthy. Client backend writes account for only %.1f%% of total buffer writes." % backend_write_pct

        return SUCCESS, result
        

    ###########################################################
    def check_unusedindexes(self):

        result = {
            "check": "unusedindexes",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "SELECT count(*) FROM pg_stat_user_indexes JOIN pg_index USING(indexrelid) WHERE idx_scan = 0 AND idx_tup_read = 0 AND idx_tup_fetch = 0 AND NOT indisprimary AND NOT indisunique AND NOT indisexclusion AND indisvalid AND indisready AND pg_relation_size(indexrelid) > 8192"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get unused indexes count: %s" % results
        try:
            unused_indexes_cnt = int(results.strip())
        except ValueError:
            return ERROR, "Invalid unused indexes count returned: %s" % results

        result["count"] = unused_indexes_cnt

        if unused_indexes_cnt == 0:
            result["message"] = "No unused indexes were found."
        else:
            result["status"] = "WARNING"
            result["message"] = "%d unused indexes were found." % unused_indexes_cnt

            if self.verbose or self.html_format:
                sql = "SELECT quote_ident(schemaname) || '.' || quote_ident(indexrelname) || ' on ' || quote_ident(schemaname) || '.' || quote_ident(relname) || " \
                      "' (' || pg_size_pretty(pg_relation_size(indexrelid)) || ')' FROM pg_stat_user_indexes JOIN pg_index USING(indexrelid) WHERE idx_scan = 0 AND idx_tup_read = 0 AND " \
                      "idx_tup_fetch = 0 AND NOT indisprimary AND NOT indisunique AND NOT indisexclusion AND indisvalid AND indisready AND pg_relation_size(indexrelid) > 8192 ORDER BY schemaname, relname, indexrelname"

                rc, details = self.executesql(sql, False)
                if rc == SUCCESS:
                    result["details"] = details or ""

        return SUCCESS, result


    ###########################################################
    def check_connectionchurn(self):

        result = {
            "check": "connectionchurn",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if self.pgversionmajor < Decimal("14.0"):
            result["status"] = "INFO"
            result["message"] = "Connection churn check requires PostgreSQL 14 or newer."
            return SUCCESS, result

        sql = "SELECT COALESCE(SUM(s.sessions), 0), COALESCE(ROUND(SUM(s.sessions::numeric / NULLIF(EXTRACT(EPOCH FROM (now() - COALESCE(s.stats_reset, pg_postmaster_start_time()))) / 3600.0, 0)), 2), 0), " \
              "COALESCE(to_char(MIN(COALESCE(s.stats_reset, pg_postmaster_start_time())), 'YYYY-MM-DD HH24:MI:SS'), 'N/A') FROM pg_stat_database s JOIN pg_database d ON d.oid = s.datid WHERE d.datallowconn"

        rc, results = self.executesql(sql, True)        
        if rc != SUCCESS:
            return rc, "Unable to get connection churn statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 3:
                raise ValueError("Expected three values.")

            total_sessions = int(values[0])
            sessions_per_hour = float(values[1])
            stats_reset = values[2]

        except (ValueError, TypeError):
            return ERROR, "Invalid connection churn statistics returned: %s" % results

        result["count"] = total_sessions
        result["message"] = "Sessions established: %s  Average session creation rate: %.2f per hour.  Statistics reset: %s" % (format(total_sessions, ","), sessions_per_hour, stats_reset)        

        return SUCCESS, result
        

    ###########################################################
    def check_connectionage(self):

        result = {
            "check": "connectionage",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "SELECT COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND state = 'active'), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND backend_start >= now() - INTERVAL '2 minutes'), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND backend_start >= now() - INTERVAL '5 minutes'), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND pid <> pg_backend_pid() AND backend_start >= now() - INTERVAL '15 minutes'), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND state = 'idle' AND backend_start IS NOT NULL), " \
              "COALESCE(CAST(EXTRACT(EPOCH FROM AVG(now() - backend_start) FILTER (WHERE backend_type = 'client backend' AND state = 'idle' AND backend_start IS NOT NULL)) AS integer), -1), " \
              "COUNT(*) FILTER (WHERE backend_type = 'client backend' AND state = 'idle' AND backend_start IS NOT NULL AND now() - state_change <= INTERVAL '1 hour') " \
              "FROM pg_stat_activity"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get connection age statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 9:
                raise ValueError("Expected nine values.")

            active_connections = int(values[0])
            idle_in_transaction = int(values[1])
            current_sessions = int(values[2])
            started_2mins = int(values[3])
            started_5mins = int(values[4])
            started_15mins = int(values[5])
            idle_connections = int(values[6])
            avgsecs = int(values[7])
            recent_activity = int(values[8])

        except (ValueError, TypeError):
            return ERROR, "Invalid connection age statistics returned: %s" % results

        stale_connections = idle_connections - recent_activity

        result["count"] = stale_connections

        if idle_connections == 0:
            result["message"] = "Active: %d.  Idle in Trans: %d.  %d current connections.  Started (last 2 mins): %d.  Started (last 5 mins): %d.  Started (last 15 mins): %d.\nNo idle client connections found." \
                                % (active_connections, idle_in_transaction, current_sessions, started_2mins, started_5mins, started_15mins)
            return SUCCESS, result
            
        avgmins = avgsecs / 60.0
        avgdays = avgsecs / 86400.0

        result["message"] = "Active: %d.  Idle in Trans: %d.  %d current connections.  Started (last 2 mins): %d.  Started (last 5 mins): %d.  Started (last 15 mins): %d.\n%d idle connections and %d of them have recent activity.  Average idle age: %.2f days (%.0f minutes)." % \
                            (active_connections, idle_in_transaction, current_sessions, started_2mins, started_5mins, started_15mins, idle_connections, recent_activity, avgdays, avgmins)

        stale_pct = (stale_connections / float(idle_connections)) * 100.0

        if stale_pct >= 25.0:
            result["status"] = "WARNING"
            result["details"] = "%d of %d idle connections (%.1f%%) have been inactive for more than 1 hour.  Warning threshold: 25%%." % (stale_connections, idle_connections, stale_pct)
            
        return SUCCESS, result
        
        
    ###########################################################
    def check_orphanedlo(self):

        result = {
            "check": "orphanedlo",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        # Large object cleanup cannot be checked on a standby.
        sql = "select pg_is_in_recovery()"
        rc, results = self.executesql(sql, True)        
        if rc != SUCCESS:
            return rc, "Unable to determine database recovery status: %s" % results

        if results.strip().lower() in ("t", "true"):
            result["status"] = "INFO"
            result["count"] = -1
            result["message"] = "N/A: Unable to detect orphaned large objects on standby servers."
            return SUCCESS, result

        # Always use vacuumlo dry-run mode. Never remove objects here.
        vacuumlo = os.path.join(self.pgbindir, "vacuumlo")

        user_clause = ""

        if self.dbhost:
            user_clause += " -h \"%s\"" % self.dbhost

        if self.dbuser:
            user_clause += " -U \"%s\"" % self.dbuser

        if self.dbport:
            user_clause += " -p %s" % self.dbport

        cmd = "\"%s\" -n%s \"%s\"" % (vacuumlo, user_clause, self.database)

        rc, results = self.executecmd(cmd, False)

        if rc != SUCCESS:
            return rc, "Unable to detect orphaned large objects: %s" % results

        try:
            if "Would remove" in results:
                numobjects = int(results.split("Would remove", 1)[1].split("large objects", 1)[0].strip())
            else:
                numobjects = 0

        except (ValueError, TypeError, IndexError):
            return ERROR, "Unable to parse vacuumlo output: %s" % results

        result["count"] = numobjects

        if numobjects == 0:
            result["message"] = "No orphaned large objects were found."
        else:
            result["status"] = "WARNING"
            result["message"] = "%d orphaned large objects were found. Consider running vacuumlo to remove them." % numobjects

        return SUCCESS, result
        

    ###########################################################
    def check_datadirusage(self):

        result = {
            "check": "datadirusage",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        if not self.local:
            result["status"] = "INFO"
            result["message"] = "N/A: PostgreSQL host is remote. Server filesystem usage is unavailable."
            return SUCCESS, result

        if self.pg_type == "rds":
            result["status"] = "INFO"
            result["message"] = "N/A: Data directory usage is unavailable for cloud instances."
            return SUCCESS, result

        if not self.datadir:
            return ERROR, "PostgreSQL data directory is not available."

        try:
            usage = shutil.disk_usage(self.datadir)
            pctused = int(math.ceil((usage.used / usage.total) * 100))

        except (OSError, ZeroDivisionError) as e:
            return ERROR, "Unable to determine data directory usage: %s" % e

        result["count"] = pctused

        if pctused > 75:
            result["status"] = "WARNING"
            result["message"] = "Data Directory Usage is high: %d%% used" % pctused
        else:
            result["message"] = "Data Directory Usage is acceptable: %d%% used" % pctused

        return SUCCESS, result
        

    ###########################################################
    def check_bloat_tables(self):

        result = {
            "check": "bloat_tables",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "WITH raw_stats AS (SELECT n.nspname AS schemaname, tbl.relname AS tablename, tbl.oid AS oid, tbl.reltuples, tbl.relpages AS heappages, " \
              "coalesce(toast.relpages, 0) AS toastpages, coalesce(toast.reltuples, 0) AS toasttuples, coalesce(substring(array_to_string(tbl.reloptions, ' ') " \
              "FROM 'fillfactor=([0-9]+)')::smallint, 100) AS fillfactor, current_setting('block_size')::numeric AS bs, CASE WHEN version() " \
              "~ '64-bit|x86_64|ppc64|ia64|amd64' THEN 8 ELSE 4 END AS ma, 24 AS page_hdr, 23 + CASE WHEN MAX(s.null_frac) > 0 THEN " \
              "(7 + count(s.attname)) / 8 ELSE 0::int END AS tpl_hdr_size, sum((1-coalesce(s.null_frac, 0)) * coalesce(s.avg_width, 0)) AS tpl_data_size " \
              "FROM pg_attribute AS att JOIN pg_class AS tbl ON att.attrelid = tbl.oid JOIN pg_namespace AS n ON n.oid = tbl.relnamespace " \
              "LEFT JOIN pg_stats AS s ON s.schemaname = n.nspname AND s.tablename = tbl.relname AND s.attname = att.attname " \
              "LEFT JOIN pg_class AS toast ON tbl.reltoastrelid = toast.oid WHERE tbl.reltuples > 100 AND att.attnum > 0 AND NOT att.attisdropped AND " \
              "tbl.relkind IN ('r','m') AND n.nspname NOT IN ('pg_catalog','information_schema') GROUP BY 1,2,3,4,5,6,7,8), est_stats AS " \
              "(SELECT *, ceil(reltuples / (((bs - page_hdr) * fillfactor / 100) / (4 + tpl_hdr_size + tpl_data_size + (2*ma) - CASE WHEN tpl_hdr_size%ma = 0 " \
              "THEN ma ELSE tpl_hdr_size%ma END - CASE WHEN ceil(tpl_data_size)::int%ma = 0 THEN ma ELSE tpl_data_size::int%ma END))) + " \
              "ceil(toasttuples / 4) AS est_tblpages_ff FROM raw_stats), final_calc AS (SELECT es.*, " \
              "GREATEST(round(100*(1-(es.est_tblpages_ff/NULLIF(es.heappages+es.toastpages,0)))::numeric,1),0) AS tbl_bloat_pct " \
              "FROM est_stats es) SELECT count(*), coalesce(sum(GREATEST((heappages+toastpages-est_tblpages_ff)*bs,0)),0)::bigint, " \
              "coalesce(string_agg(format('%I.%I', schemaname, tablename), ', ' ORDER BY schemaname, tablename), '') " \
              "FROM final_calc WHERE tbl_bloat_pct > 30 AND ((heappages+toastpages-est_tblpages_ff)*bs) > 100*1024*1024"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get table bloat statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 3:
                raise ValueError("Expected three values.")

            bloated_tables = int(values[0])
            wasted_bytes = int(values[1])
            table_names = values[2]

        except (ValueError, TypeError):
            return ERROR, "Invalid table bloat statistics returned: %s" % results

        wasted_mb = wasted_bytes / 1024.0 / 1024.0

        result["count"] = bloated_tables
        result["details"] = table_names

        if bloated_tables > 0:
            result["status"] = "WARNING"
            result["message"] = "%d bloated table(s) detected. Estimated wasted space: %.1f MB." % (bloated_tables, wasted_mb)
        else:
            result["message"] = "No bloated tables detected."

        return SUCCESS, result


    ###########################################################
    def check_bloat_indexes(self):

        result = {
            "check": "bloat_indexes",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "WITH index_cols AS (SELECT i.indexrelid, SUM(COALESCE(s.avg_width, 8)) AS avg_col_width FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace CROSS JOIN LATERAL unnest(i.indkey) AS k(attnum) JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum LEFT JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = t.relname AND s.attname = a.attname WHERE k.attnum > 0 GROUP BY i.indexrelid), index_info AS (SELECT n.nspname AS schemaname, ct.relname AS tablename, ci.relname AS indexname, ci.oid AS idxoid, COALESCE((regexp_match(array_to_string(ci.reloptions, ','), 'fillfactor=([0-9]+)'))[1]::int, 90) AS fillfactor, ci.reltuples, pg_relation_size(ci.oid) AS real_size_bytes, COALESCE(ic.avg_col_width,0) AS avg_col_width, (t.n_tup_ins + t.n_tup_upd + t.n_tup_del)::bigint AS total_writes, s.idx_scan::bigint AS scans FROM pg_index i JOIN pg_class ci ON ci.oid = i.indexrelid JOIN pg_class ct ON ct.oid = i.indrelid JOIN pg_namespace n ON n.oid = ct.relnamespace JOIN pg_stat_all_indexes s ON s.indexrelid = i.indexrelid JOIN pg_stat_user_tables t ON t.relid = i.indrelid LEFT JOIN index_cols ic ON ic.indexrelid = i.indexrelid WHERE ci.relam = (SELECT oid FROM pg_am WHERE amname = 'btree') AND n.nspname NOT IN ('pg_catalog','information_schema')), calc AS (SELECT *, (reltuples * (avg_col_width + 24 + 8))::bigint AS estimated_needed_bytes FROM index_info), evaluated AS (SELECT *, GREATEST(real_size_bytes - estimated_needed_bytes,0) AS estimated_bloat_bytes, ROUND(100 * GREATEST(real_size_bytes - estimated_needed_bytes,0)::numeric / NULLIF(real_size_bytes,0), 1) AS bloat_pct FROM calc) SELECT count(*) FILTER (WHERE real_size_bytes > 50*1024*1024 AND bloat_pct > 30), coalesce(sum(estimated_bloat_bytes) FILTER (WHERE real_size_bytes > 50*1024*1024 AND bloat_pct > 30),0)::bigint, coalesce(string_agg(format('%I.%I', schemaname, indexname), ', ' ORDER BY schemaname, indexname) FILTER (WHERE real_size_bytes > 50*1024*1024 AND bloat_pct > 30), '') FROM evaluated"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get index bloat statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 3:
                raise ValueError("Expected three values.")

            bloated_indexes = int(values[0])
            wasted_bytes = int(values[1])
            index_names = values[2]

        except (ValueError, TypeError):
            return ERROR, "Invalid index bloat statistics returned: %s" % results

        wasted_mb = wasted_bytes / 1024.0 / 1024.0

        result["count"] = bloated_indexes
        result["details"] = index_names

        if bloated_indexes > 0:
            result["status"] = "WARNING"
            result["message"] = "%d bloated index(es) detected. Estimated wasted space: %.1f MB." % (bloated_indexes, wasted_mb)
        else:
            result["message"] = "No bloated indexes detected."

        return SUCCESS, result


    ###########################################################
    def check_bloat_toast(self):

        result = {
            "check": "bloat_toast",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        extension_sql = "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_freespacemap')"
        rc, extension_result = self.executesql(extension_sql, True)
        if rc != SUCCESS:
            return rc, "Unable to determine whether pg_freespacemap is installed: %s" % extension_result

        has_freespacemap = extension_result.strip().lower() in ("t", "true")

        if has_freespacemap:
            sql = "WITH toast_tables AS (SELECT pn.nspname AS schemaname, parent.relname AS tablename, toast.oid AS toast_oid, " \
                  "pg_relation_size(toast.oid) AS heap_bytes, pg_total_relation_size(toast.oid) AS total_bytes, " \
                  "COALESCE(s.n_live_tup,0)::bigint AS live_tuples, COALESCE(s.n_dead_tup,0)::bigint AS dead_tuples " \
                  "FROM pg_class parent JOIN pg_namespace pn ON pn.oid = parent.relnamespace JOIN pg_class toast ON toast.oid = parent.reltoastrelid " \
                  "LEFT JOIN pg_stat_all_tables s ON s.relid = toast.oid WHERE parent.relkind IN ('r','m') " \
                  "AND pn.nspname NOT IN ('pg_catalog','information_schema') AND pg_relation_size(toast.oid) > 100*1024*1024), " \
                  "free_space AS (SELECT t.*, COALESCE(f.free_bytes,0)::bigint AS free_bytes FROM toast_tables t " \
                  "LEFT JOIN LATERAL (SELECT sum(pg_freespace(t.toast_oid::regclass,g.blkno))::bigint AS free_bytes " \
                  "FROM generate_series(0,(t.heap_bytes/current_setting('block_size')::bigint)-1) AS g(blkno)) f ON true), " \
                  "evaluated AS (SELECT *, CASE WHEN heap_bytes > 0 THEN 100.0*free_bytes/heap_bytes ELSE 0 END AS free_pct, " \
                  "CASE WHEN live_tuples+dead_tuples > 0 THEN 100.0*dead_tuples/(live_tuples+dead_tuples) ELSE 0 END AS dead_pct FROM free_space) " \
                  "SELECT count(*) FILTER (WHERE (free_bytes > 100*1024*1024 AND free_pct > 30) OR dead_pct > 30), " \
                  "COALESCE(sum(free_bytes) FILTER (WHERE free_bytes > 100*1024*1024 AND free_pct > 30),0)::bigint, " \
                  "count(*) FILTER (WHERE dead_pct > 30), " \
                  "COALESCE(string_agg(format('%I.%I: ',schemaname,tablename) || round(free_pct::numeric,1)::text || '% free space, ' || " \
                  "round(free_bytes/1048576.0,1)::text || ' MB reusable, ' || round(dead_pct::numeric,1)::text || " \
                  "'% estimated dead tuples, ' || dead_tuples::text || ' dead tuples, ' || " \
                  "round(heap_bytes/1048576.0,1)::text || ' MB heap, ' || round(total_bytes/1048576.0,1)::text || " \
                  "' MB total' || CASE WHEN dead_pct > 30 THEN ' [VACUUM recommended]' ELSE '' END, " \
                  "E'\\n' ORDER BY schemaname,tablename) " \
                  "FILTER (WHERE (free_bytes > 100*1024*1024 AND free_pct > 30) OR dead_pct > 30),'') FROM evaluated"
        else:
            sql = "WITH toast_tables AS (SELECT pn.nspname AS schemaname, parent.relname AS tablename, " \
                  "pg_relation_size(toast.oid) AS heap_bytes, pg_total_relation_size(toast.oid) AS total_bytes, " \
                  "COALESCE(s.n_live_tup,0)::bigint AS live_tuples, COALESCE(s.n_dead_tup,0)::bigint AS dead_tuples " \
                  "FROM pg_class parent JOIN pg_namespace pn ON pn.oid = parent.relnamespace JOIN pg_class toast ON toast.oid = parent.reltoastrelid " \
                  "LEFT JOIN pg_stat_all_tables s ON s.relid = toast.oid WHERE parent.relkind IN ('r','m') " \
                  "AND pn.nspname NOT IN ('pg_catalog','information_schema') AND pg_relation_size(toast.oid) > 100*1024*1024), " \
                  "evaluated AS (SELECT *, CASE WHEN live_tuples+dead_tuples > 0 THEN " \
                  "100.0*dead_tuples/(live_tuples+dead_tuples) ELSE 0 END AS dead_pct FROM toast_tables) " \
                  "SELECT count(*) FILTER (WHERE dead_pct > 30), 0::bigint, count(*) FILTER (WHERE dead_pct > 30), " \
                  "COALESCE(string_agg(format('%I.%I: ',schemaname,tablename) || round(dead_pct::numeric,1)::text || " \
                  "'% estimated dead tuples, ' || dead_tuples::text || ' dead tuples, ' || " \
                  "round(heap_bytes/1048576.0,1)::text || ' MB heap, ' || round(total_bytes/1048576.0,1)::text || " \
                  "' MB total [VACUUM recommended]', " \
                  "E'\\n' ORDER BY schemaname,tablename) FILTER (WHERE dead_pct > 30),'') FROM evaluated"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get TOAST bloat statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 4:
                raise ValueError("Expected four values.")

            bloated_toast_tables = int(values[0])
            reusable_bytes = int(values[1])
            vacuum_tables = int(values[2])
            toast_details = values[3]

        except (ValueError, TypeError):
            return ERROR, "Invalid TOAST bloat statistics returned: %s" % results

        result["count"] = bloated_toast_tables
        result["details"] = toast_details

        if has_freespacemap:
            if vacuum_tables > 0:
                reusable_mb = reusable_bytes / 1024.0 / 1024.0
                result["status"] = "WARNING"
                result["message"] = "%d TOAST table(s) have elevated estimated dead-tuple ratios. VACUUM recommended. Estimated reusable space: %.1f MB." % (vacuum_tables, reusable_mb)
            elif bloated_toast_tables > 0:
                reusable_mb = reusable_bytes / 1024.0 / 1024.0
                result["status"] = "WARNING"
                result["message"] = "%d TOAST table(s) have significant reusable free space. VACUUM is not recommended based on free space alone. Estimated reusable space: %.1f MB." % (bloated_toast_tables, reusable_mb)
            else:
                result["message"] = "No significant TOAST free-space bloat or elevated dead-tuple ratios detected (pg_freespacemap available)."
        else:
            if vacuum_tables > 0:
                result["status"] = "WARNING"
                result["message"] = "%d TOAST table(s) have elevated estimated dead-tuple ratios. VACUUM recommended. pg_freespacemap is unavailable; free-space estimates cannot be reported." % vacuum_tables
            else:
                result["status"] = "INFO"
                result["message"] = "No elevated TOAST dead-tuple ratios detected. pg_freespacemap is unavailable; using the reduced-information fallback."

        return SUCCESS, result
        
        
    ###########################################################
    def check_maintenance_candidates(self):

        result = {
            "check": "maintenance_candidates",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "WITH settings AS (SELECT current_setting('autovacuum_freeze_max_age')::bigint AS freeze_max_age), " \
              "candidates AS (SELECT n.nspname AS schemaname, c.relname AS tablename, " \
              "COALESCE(u.n_dead_tup, 0) AS dead_tuples, COALESCE(u.n_live_tup, 0) AS live_tuples, " \
              "COALESCE(u.n_mod_since_analyze, 0) AS modifications_since_analyze, " \
              "GREATEST(u.last_vacuum, u.last_autovacuum) AS last_vacuum, " \
              "GREATEST(u.last_analyze, u.last_autoanalyze) AS last_analyze, " \
              "age(c.relfrozenxid) AS xid_age, " \
              "CASE WHEN COALESCE(u.n_live_tup, 0) + COALESCE(u.n_dead_tup, 0) > 0 " \
              "THEN 100.0 * COALESCE(u.n_dead_tup, 0) / (u.n_live_tup + u.n_dead_tup) ELSE 0 END AS dead_ratio, " \
              "CASE WHEN COALESCE(u.n_live_tup, 0) > 0 " \
              "THEN 100.0 * COALESCE(u.n_mod_since_analyze, 0) / u.n_live_tup " \
              "WHEN COALESCE(u.n_mod_since_analyze, 0) > 0 THEN 100 ELSE 0 END AS modifications_ratio, " \
              "s.freeze_max_age FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace " \
              "LEFT JOIN pg_stat_user_tables u ON u.relid = c.oid CROSS JOIN settings s " \
              "WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema') " \
              "AND n.nspname NOT LIKE 'pg_toast%'), evaluated AS (SELECT *, " \
              "(last_vacuum IS NULL OR last_vacuum < now() - interval '7 days' OR dead_ratio >= 2) AS vacuum_due, " \
              "(last_analyze IS NULL OR last_analyze < now() - interval '7 days' " \
              "OR dead_ratio >= 3 OR modifications_ratio >= 2) AS analyze_due, " \
              "(xid_age >= freeze_max_age * 0.80) AS freeze_due " \
              "FROM candidates) SELECT " \
              "count(*) FILTER (WHERE vacuum_due AND analyze_due), " \
              "count(*) FILTER (WHERE vacuum_due AND NOT analyze_due), " \
              "count(*) FILTER (WHERE analyze_due AND NOT vacuum_due), " \
              "count(*) FILTER (WHERE freeze_due), " \
              "count(*) FILTER (WHERE vacuum_due OR analyze_due OR freeze_due), " \
              "COALESCE(string_agg(format('%I.%I', schemaname, tablename), ', ' ORDER BY schemaname, tablename) FILTER (WHERE vacuum_due AND analyze_due), ''), " \
              "COALESCE(string_agg(format('%I.%I', schemaname, tablename), ', ' ORDER BY schemaname, tablename) FILTER (WHERE vacuum_due AND NOT analyze_due), ''), " \
              "COALESCE(string_agg(format('%I.%I', schemaname, tablename), ', ' ORDER BY schemaname, tablename) FILTER (WHERE analyze_due AND NOT vacuum_due), ''), " \
              "COALESCE(string_agg(format('%I.%I', schemaname, tablename), ', ' ORDER BY schemaname, tablename) FILTER (WHERE freeze_due), '') " \
              "FROM evaluated"

        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to get maintenance candidate statistics: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 9:
                raise ValueError("Expected nine values.")

            vacuum_analyze_count = int(values[0])
            vacuum_count = int(values[1])
            analyze_count = int(values[2])
            freeze_count = int(values[3])
            total_count = int(values[4])

            vacuum_analyze_tables = values[5].strip()
            vacuum_tables = values[6].strip()
            analyze_tables = values[7].strip()
            freeze_tables = values[8].strip()
            
        except (ValueError, TypeError):
            return ERROR, "Invalid maintenance candidate statistics returned: %s" % results

        result["count"] = total_count

        if result["count"] > 0:
            result["status"] = "WARNING"
            result["message"] = "Maintenance candidates detected: VACUUM/ANALYZE: %d, VACUUM: %d, ANALYZE: %d, FREEZE: %d" % (vacuum_analyze_count, vacuum_count, analyze_count, freeze_count)

            if vacuum_analyze_tables:
                result["details"] += "VACUUM/ANALYZE: " + vacuum_analyze_tables + "\n"

            if vacuum_tables:
                result["details"] += "VACUUM: " + vacuum_tables + "\n"

            if analyze_tables:
                result["details"] += "ANALYZE: " + analyze_tables + "\n"

            if freeze_tables:
                result["details"] += "FREEZE: " + freeze_tables + "\n"

            result["details"] = result["details"].rstrip()

        else:
            result["message"] = "No VACUUM, ANALYZE, or FREEZE candidates detected."

        return SUCCESS, result


    ###########################################################
    def check_configrecommendations(self):

        result = {
            "check": "configrecommendations",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "SELECT " \
              "(SELECT setting FROM pg_settings WHERE name = 'autovacuum'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'checkpoint_completion_target'), " \
              "current_setting('data_checksums'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'idle_in_transaction_session_timeout'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'log_checkpoints'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'log_lock_waits'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'log_min_duration_statement'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'log_temp_files'), " \
              "EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements'), " \
              "'pg_stat_statements' = ANY(string_to_array(replace(current_setting('shared_preload_libraries'), ' ', ''), ',')), " \
              "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'), " \
              "(SELECT setting FROM pg_settings WHERE name = 'track_activity_query_size')"

        rc, results = self.executesql(sql, True)        
        if rc != SUCCESS:
            return rc, "Unable to get PostgreSQL configuration recommendations: %s" % results

        try:
            values = results.strip().split("|")

            if len(values) != 12:
                raise ValueError("Expected twelve configuration values.")

            autovacuum = values[0]
            checkpoint_completion_target = float(values[1])
            data_checksums = values[2]
            idle_in_transaction_session_timeout = int(values[3])
            log_checkpoints = values[4]
            log_lock_waits = values[5]
            log_min_duration_statement = int(values[6])
            log_temp_files = int(values[7])
            extension_available = values[8].lower() == "t"
            extension_preloaded = values[9].lower() == "t"
            extension_installed = values[10].lower() == "t"
            track_activity_query_size = int(values[11])

        except (ValueError, TypeError):
            return ERROR, "Invalid PostgreSQL configuration values returned: %s" % results

        details = []
        warning_count = 0

        if autovacuum != "on":
            details.append("autovacuum is off.")
            warning_count += 1

        if checkpoint_completion_target <= 0.9:
            details.append("checkpoint_completion_target is less than optimal (value=%s)." % values[1])
            warning_count += 1

        if data_checksums != "on":
            details.append("data checksums are off.")
            warning_count += 1

        if idle_in_transaction_session_timeout == 0:
            details.append("idle_in_transaction_session_timeout is turned off.")
            warning_count += 1

        if log_checkpoints != "on":
            details.append("log_checkpoints is off.")
            warning_count += 1

        if log_lock_waits != "on":
            details.append("log_lock_waits is off.")
            warning_count += 1

        if log_min_duration_statement == -1:
            details.append("log_min_duration_statement is off.")
            warning_count += 1

        if log_temp_files == -1:
            details.append("log_temp_files is off.")
            warning_count += 1

        if not extension_available:
            details.append("pg_stat_statements is not available on this PostgreSQL server.")
            warning_count += 1
        elif not extension_preloaded:
            if extension_installed:
                details.append("pg_stat_statements is installed but not listed in shared_preload_libraries. A PostgreSQL restart is required after adding it.")
            else:
                details.append("pg_stat_statements is neither preloaded nor installed in this database.")
            warning_count += 1
        elif not extension_installed:
            details.append("pg_stat_statements is preloaded but not installed in database '%s'. Run CREATE EXTENSION." % self.database)
            warning_count += 1

        if track_activity_query_size < 8192:
            details.append("track_activity_query_size may need to be increased; query text may be truncated (value=%d). Changing this setting requires a PostgreSQL restart." % track_activity_query_size)
            warning_count += 1

        result["count"] = warning_count
        result["details"] = "\n".join(details)

        if warning_count:
            result["status"] = "WARNING"
            result["message"] = "%d PostgreSQL configuration recommendation(s) need attention." % warning_count
        else:
            result["message"] = "PostgreSQL configuration recommendations are satisfied."

        return SUCCESS, result
        
        
    ###########################################################
    def check_replication(self):

        result = {
            "check": "replication",
            "status": "OK",
            "count": 0,
            "message": "",
            "details": ""
        }

        sql = "select pg_is_in_recovery()"

        rc, results = self.executesql(sql, True)        

        if rc != SUCCESS:
            result["status"] = "INFO"
            result["message"] = "Replication status unavailable: %s" % results
            return SUCCESS, result

        in_recovery = results.strip().lower() in ("t", "true")
        if in_recovery:

            sql = "select status || '|' || coalesce(received_lsn::text, 'N/A') || '|' || coalesce(latest_end_lsn::text, 'N/A') || '|' || coalesce(extract(epoch from (clock_timestamp() - last_msg_receipt_time))::text, '-1') from pg_stat_wal_receiver"
            rc, results = self.executesql(sql, False)            
            if rc != SUCCESS:
                result["status"] = "INFO"
                result["message"] = "Standby detected, but WAL receiver status is unavailable."
                return SUCCESS, result

            if not results.strip():
                result["status"] = "WARNING"
                result["message"] = "Standby is in recovery but no WAL receiver is active."
                return SUCCESS, result

            try:
                cols = results.strip().split("|")

                if len(cols) != 4:
                    raise ValueError("Expected four values.")

                last_msg_age = float(cols[3])

            except (ValueError, IndexError):
                result["status"] = "INFO"
                result["message"] = "Standby WAL receiver is active, but message freshness could not be determined."
                result["details"] = results.strip()
                return SUCCESS, result

            result["count"] = 1
            result["details"] = results.strip()

            if last_msg_age < 0:
                result["status"] = "INFO"
                result["message"] = "Standby WAL receiver is active, but no WAL message receipt time is available."
            elif last_msg_age > DEFAULT_REPLICATION_LAG_SECS:
                result["status"] = "WARNING"
                result["message"] = "Standby WAL receiver has not received a message for %.1f seconds." % last_msg_age
            else:
                result["message"] = "Standby is receiving WAL. Last message received %.1f seconds ago." % last_msg_age

            return SUCCESS, result
            
        sql = "select count(*), coalesce(string_agg(concat_ws('|', coalesce(application_name, 'N/A'), coalesce(state, 'N/A'), " \
              "coalesce(sync_state, 'N/A'), coalesce(sent_lsn::text, 'N/A'), coalesce(write_lsn::text, 'N/A'), coalesce(flush_lsn::text, 'N/A'), " \
              "coalesce(replay_lsn::text, 'N/A'), coalesce(write_lag::text, 'N/A'), coalesce(flush_lag::text, 'N/A'), " \
              "coalesce(extract(epoch from replay_lag)::text, 'N/A'), coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), " \
              "replay_lsn)::text, 'N/A')), E'\\n'), '') from pg_stat_replication"
              
        rc, results = self.executesql(sql, False)            
        if rc != SUCCESS:
            result["status"] = "INFO"
            result["message"] = "Primary detected, but replication status is unavailable: %s" % results
            return SUCCESS, result

        try:
            values = results.strip().split("|", 1)
            replica_count = int(values[0])
            raw_details = values[1] if len(values) > 1 else ""
        except ValueError:
            return ERROR, "Invalid replication information returned: %s" % results

        result["count"] = replica_count

        if replica_count == 0:
            result["status"] = "INFO"
            result["message"] = "Primary detected. No streaming replicas are connected."
            return SUCCESS, result

        details = []

        for line in raw_details.splitlines():

            fields = line.split("|")

            if len(fields) != 11:
                return ERROR, "Invalid replication detail information returned: %s" % line

            application_name = fields[0]
            state = fields[1]
            sync_state = fields[2]
            sent_lsn = fields[3]
            write_lsn = fields[4]
            flush_lsn = fields[5]
            replay_lsn = fields[6]
            write_lag = fields[7]
            flush_lag = fields[8]
            replay_lag = fields[9]
            replay_lag_bytes = fields[10]

            detail = "%s  state=%s  sync=%s  sent=%s  write=%s  flush=%s  replay=%s  write_lag=%s  flush_lag=%s  replay_lag=%s  replay_lag_bytes=%s" % (application_name, state, sync_state, sent_lsn, write_lsn, flush_lsn, replay_lsn, write_lag, flush_lag, replay_lag, replay_lag_bytes)

            details.append(detail)
            if state == "streaming":

                if replay_lag != "N/A":
                    try:
                        if float(replay_lag) > DEFAULT_REPLICATION_LAG_SECS:
                            result["status"] = "WARNING"
                    except ValueError:
                        result["status"] = "WARNING"

                if replay_lag_bytes != "N/A":
                    try:
                        if float(replay_lag_bytes) > DEFAULT_REPLICATION_LAG_BYTES:
                            result["status"] = "WARNING"
                    except ValueError:
                        result["status"] = "WARNING"            

        result["details"] = "\n".join(details)

        if not any("state=streaming" in detail for detail in details):
            result["status"] = "WARNING"
            result["message"] = "%d replication connection(s) detected, but none are streaming." % replica_count
        elif result["status"] == "WARNING":
            result["message"] = "Replication lag exceeds configured thresholds."
        else:
            result["message"] = "%d streaming replication connection(s) detected. No significant replication lag detected." % replica_count
            
        return SUCCESS, result
        
        
    ###########################################################
    def run_checks(self):

        results = []

        checks = [
            self.check_load,
            self.check_datadirusage,
            self.check_configrecommendations,
            self.check_connectionchurn,
            self.check_waitslocks,
            self.check_idleintrans,
            self.check_idleconns,
            self.check_longquery,
            self.check_activeconns,
            self.check_connectionage,
            self.check_maxconns,
            self.check_cachehit,
            self.check_dbconflicts,
            self.check_checkpoints,
            self.check_bgwriter,
            self.check_unusedindexes,
            self.check_orphanedlo,
            self.check_bloat_tables,
            self.check_bloat_indexes,
            self.check_maintenance_candidates,
            self.check_bloat_toast,
            self.check_replication
        ]        
        
        for check in checks:
            rc, result = check()

            if rc != SUCCESS:
                return rc, result

            if result["status"] == "WARNING":
                marker = MARK_WARN
            elif result["status"] == "INFO":
                marker = MARK_INFO
            else:
                marker = MARK_OK

            if result["check"] == "waitslocks" and result["status"] == "WARNING" and result["details"].strip():
                details = result["details"].splitlines()
                print(marker + result["message"] + "  " + details[0])

                if self.verbose:
                    for detail in details[1:]:
                        print(MARK_VERB + detail)
            else:
                for message in result["message"].splitlines():
                    print(marker + message)                

                if result["details"]:
                    if result["check"] in ("bloat_tables", "bloat_indexes", "bloat_toast", "maintenance_candidates", "unusedindexes"):
                        if self.verbose:
                            for detail in result["details"].splitlines():
                                print(MARK_VERB + detail)
                    else:
                        for detail in result["details"].splitlines():
                            if result["check"] in ("checkpoints", "bgwriter", "replication"):
                                if self.verbose:
                                    print(MARK_VERB + detail)
                            elif result["status"] == "WARNING":
                                print(MARK_WARN + detail)
                            else:
                                print(MARK_INFO + detail)
                                
            results.append(result)

        print(MARK_INFO + "%d checks completed successfully." % len(results))

        return SUCCESS, results        


    ###########################################################
    def generate_html_report(self, results):

        hostname = self.dbhost if self.dbhost else platform.uname()[1]
        generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pgversion = str(self.pgversionminor)

        status_counts = {
            "OK": 0,
            "WARNING": 0,
            "INFO": 0
        }

        for result in results:
            status = result.get("status", "INFO").upper()
            if status in status_counts:
                status_counts[status] += 1

        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PostgreSQL Smart Check Report</title>
<style>
    body {
        font-family: Arial, Helvetica, sans-serif;
        background: #f4f6f9;
        color: #263238;
        margin: 0;
        padding: 25px;
    }
    .container {
        max-width: 1400px;
        margin: auto;
    }
    .header {
        background: #263b50;
        color: white;
        padding: 25px 30px;
        border-radius: 8px;
        margin-bottom: 20px;
    }
    .header h1 {
        margin: 0 0 15px 0;
        font-size: 28px;
    }
    .metadata {
        color: #dce5ed;
        line-height: 1.8;
        font-size: 14px;
    }
    .summary {
        display: flex;
        gap: 15px;
        margin-bottom: 20px;
        flex-wrap: wrap;
    }
    .summary-card {
        background: white;
        border-radius: 8px;
        padding: 18px 24px;
        flex: 1;
        min-width: 140px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.08);
    }
    .summary-card .label {
        font-size: 13px;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .summary-card .value {
        font-size: 30px;
        font-weight: bold;
        margin-top: 5px;
    }
    .ok { color: #16803c; }
    .warning { color: #c62828; }
    .info { color: #2563a6; }
    .report-section {
        background: white;
        border-radius: 8px;
        padding: 20px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.08);
    }
    .report-section h2 {
        margin: 0 0 15px 0;
        font-size: 21px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th {
        background: #e9eef3;
        color: #263b50;
        text-align: left;
        padding: 12px;
        font-size: 13px;
    }
    td {
        padding: 12px;
        border-bottom: 1px solid #e5e9ee;
        vertical-align: top;
        font-size: 14px;
    }
    tr:hover {
        background: #f8fafc;
    }
    .status {
        font-weight: bold;
        white-space: nowrap;
    }
    .check-name {
        font-weight: bold;
        white-space: nowrap;
    }
    details {
        margin-top: 8px;
    }
    summary {
        color: #2563a6;
        cursor: pointer;
        font-size: 13px;
        font-weight: bold;
    }
    pre {
        background: #f4f6f9;
        border: 1px solid #e1e6ec;
        border-radius: 5px;
        padding: 12px;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        font-family: Consolas, monospace;
        font-size: 12px;
        line-height: 1.5;
    }
    .footer {
        text-align: center;
        color: #64748b;
        font-size: 12px;
        margin-top: 20px;
    }
    @media (max-width: 700px) {
        body { padding: 10px; }
        .report-section { padding: 10px; overflow-x: auto; }
        table { min-width: 650px; }
    }
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>PostgreSQL Smart Check Report</h1>
        <div class="metadata">
            <strong>Host:</strong> {{HOST}}<br>
            <strong>Database:</strong> {{DATABASE}}<br>
            <strong>PostgreSQL Version:</strong> {{PGVERSION}}<br>
            <strong>Generated:</strong> {{GENERATED}}            
        </div>
    </div>
    <div class="summary">
        <div class="summary-card">
            <div class="label">Total Checks</div>
            <div class="value">{{TOTAL}}</div>
        </div>
        <div class="summary-card">
            <div class="label">OK</div>
            <div class="value ok">{{OKCOUNT}}</div>
        </div>
        <div class="summary-card">
            <div class="label">Warnings</div>
            <div class="value warning">{{WARNCOUNT}}</div>
        </div>
        <div class="summary-card">
            <div class="label">Information</div>
            <div class="value info">{{INFOCOUNT}}</div>
        </div>
    </div>
    <div class="report-section">
        <h2>Health Checks</h2>
        <table>
            <thead>
                <tr>
                    <th style="width: 110px;">Status</th>
                    <th style="width: 190px;">Category</th>
                    <th>Analysis</th>
                </tr>
            </thead>
            <tbody>
                {{ROWS}}
            </tbody>
        </table>
    </div>
    <div class="footer">
        Generated by pg_smartcheck version {{VERSION}}
    </div>
</div>
</body>
</html>
"""

        html_content = html_content.replace("{{HOST}}", html.escape(str(hostname)))
        html_content = html_content.replace("{{DATABASE}}", html.escape(str(self.database)))
        html_content = html_content.replace("{{PGVERSION}}", html.escape(pgversion))
        html_content = html_content.replace("{{GENERATED}}", generated)
        html_content = html_content.replace("{{TOTAL}}", str(len(results)))
        html_content = html_content.replace("{{OKCOUNT}}", str(status_counts["OK"]))
        html_content = html_content.replace("{{WARNCOUNT}}", str(status_counts["WARNING"]))
        html_content = html_content.replace("{{INFOCOUNT}}", str(status_counts["INFO"]))
        html_content = html_content.replace("{{VERSION}}", str(VERSION))        

        rows = []

        for result in results:
            status = result.get("status", "INFO").upper()
            check = result.get("check", "unknown")
            message = result.get("message", "")
            details = result.get("details", "")

            status_class = status.lower()
            status_text = html.escape(status)

            check_name = html.escape(check.replace("_", " ").title())
            message_text = html.escape(str(message))

            detail_html = ""

            if details and str(details).strip():
                detail_html = (
                    "<details>"
                    "<summary>Show details</summary>"
                    "<pre>%s</pre>"
                    "</details>"
                    % html.escape(str(details))
                )

            rows.append(
                "<tr>"
                "<td class='status %s'>%s</td>"
                "<td class='check-name'>%s</td>"
                "<td>%s%s</td>"
                "</tr>"
                % (status_class, status_text, check_name, message_text, detail_html)
            )

        html_content = html_content.replace("{{ROWS}}", "\n".join(rows))

        try:
            with open(self.reportfile, "w", encoding="utf-8") as f:
                f.write(html_content)

        except OSError as e:
            return ERROR, "Unable to write HTML report '%s': %s" % (self.reportfile, e)

        return SUCCESS, "HTML report generated: %s" % self.reportfile

    
    ###########################################################
    def set_dbinfo(self, options, argv):

        self.dbhost           = options.dbhost
        self.dbport           = options.dbport if options.dbport else "5432"
        self.dbuser           = options.dbuser
        self.database         = options.database

        self.waitslocks       = options.waitslocks
        self.longquerymins    = options.longquerymins
        self.idleintransmins  = options.idleintransmins
        self.idleconnmins     = options.idleconnmins
        self.vcpus            = options.vcpus

        self.html_format      = options.html_format        
        self.verbose          = options.verbose
        self.debug            = options.debug

        if os.name == "posix":
            self.opsys = "posix"
            self.dir_delim = "/"
        elif os.name == "nt":
            self.opsys = "nt"
            self.dir_delim = "\\"
        else:
            return ERROR, "Unsupported platform."

        self.tempfile = os.path.join(self.tempdir, "%s_temp.sql" % self.pid)
        self.workfile = os.path.join(self.tempdir, "%s_stats.sql" % self.pid)
        self.workfile_deferred = os.path.join(self.tempdir, "%s_stats_deferred.sql" % self.pid)

        if self.html_format:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            self.reportfile = os.path.join(os.getcwd(), "pg_smartcheck_%s.html" % timestamp)
        else:
            self.reportfile = ""
            
        self.connstring = ""
        if self.dbhost:
            self.connstring += " -h %s" % self.dbhost
        if self.database:
            self.connstring += " -d %s" % self.database
        if self.dbport:
            self.connstring += " -p %s" % self.dbport
        if self.dbuser:
            self.connstring += " -U %s" % self.dbuser

        #self.local = (self.dbhost == "" or "localhost" in self.dbhost or "127.0.0.1" in self.dbhost)
        # Determine whether the PostgreSQL connection target is local.
        # Be conservative: ambiguous connection targets are treated as remote.
        effective_host = self.dbhost or os.environ.get("PGHOST", "")
        effective_hostaddr = os.environ.get("PGHOSTADDR", "")        
        service_name = os.environ.get("PGSERVICE", "")

        self.local = False

        if service_name:
            hosts = []
        elif effective_hostaddr:        
            hosts = effective_hostaddr.split(",")
        elif effective_host:
            hosts = effective_host.split(",")
        else:
            # No host or service override means libpq uses its local default.
            self.local = True
            hosts = []

        if hosts:
            self.local = True

            for host in hosts:
                host = host.strip().lower().strip("[]")

                if host == "localhost":
                    continue

                if self.opsys == "posix" and host.startswith("/"):
                    continue

                try:
                    if not ipaddress.ip_address(host).is_loopback:
                        self.local = False
                        break
                except ValueError:
                    self.local = False
                    break        
        
        rc, errors = self.validate_parms()
        if rc != SUCCESS:
            return rc, errors

        self.dbpass = os.environ.get("PGPASSWORD", "")            

        cmd = "where psql" if self.opsys == "nt" else "which psql"
        rc, results = self.executecmd(cmd, True)
        if rc != SUCCESS:
            return rc, "Unable to locate psql in PATH: %s" % results

        psql_paths = [line.strip() for line in results.splitlines() if line.strip()]
        psql_path = next((line for line in psql_paths if "psql" in line.lower()), "")

        if not psql_path:
            return ERROR, "psql must be available in PATH."

        self.pgbindir = os.path.dirname(psql_path)

        try:
            self.connection = psycopg2.connect(
                dbname=self.database,
                user=self.dbuser or None,
                host=self.dbhost or None,
                port=self.dbport or None,
                application_name="pg_smartcheck",
                connect_timeout=10
            )
            self.connection.autocommit = True
            self.connected = True

        except psycopg2.Error as e:
            return ERROR, "Unable to connect to PostgreSQL database '%s': %s" % (self.database, e)

        rc, results = self.get_pgversion()
        if rc != SUCCESS:
            return rc, results
            
        rc, results = self.get_configinfo()
        if rc != SUCCESS:
            return rc, results

        rc, results = self.get_pgbindir()
        if rc != SUCCESS and self.verbose:
            print("WARNING: Unable to determine pg_config bindir: %s" % results)

        if self.verbose:
            print("[VERB]  PG Version=%s  shared_buffers=%s MB  maintenance_work_mem=%s MB  work_mem=%s MB  shared_preload_libraries=%s PG Bind Directory=%s" \
                  % (str(self.pgversionminor), self.shared_buffers, self.maint_work_mem, self.work_mem, self.shared_preload_libraries, self.pgbindir))            

        sql = "SELECT 1"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to connect to PostgreSQL database '%s': %s" % (self.database, results)

        self.connected = True

        return SUCCESS, ""


    ###########################################################
    def get_pgbindir(self):

        cmd = "pg_config --bindir"
        rc, results = self.executecmd(cmd, True)

        if rc != SUCCESS:
            # Fall back to the directory containing psql.
            if self.pgbindir:
                return SUCCESS, self.pgbindir

            return rc, "Unable to determine PostgreSQL binary directory: %s" % results

        self.pgbindir = str(results).strip().splitlines()[0]

        return SUCCESS, self.pgbindir


    ###########################################################
    def get_datadir(self):

        sql = "show data_directory"
        rc, results = self.executesql(sql, True)
        if rc != SUCCESS:
            return rc, "Unable to determine PostgreSQL data directory: %s" % results

        self.datadir = str(results).strip()
        self.waldir = os.path.join(self.datadir, "pg_wal" if self.pgversionmajor > Decimal("9.6") else "pg_xlog")

        if self.verbose:
            print("PG Data Directory = %s" % self.datadir)
            print("PG WAL Directory = %s" % self.waldir)

        return SUCCESS, self.datadir
        

    ###########################################################
    def cleanup(self):
        if self.connection:
            try:
                self.connection.close()
            except psycopg2.Error:
                pass
            self.connection = None

        self.connected = False

        if self.tempfile and os.path.isfile(self.tempfile):
            try:
                os.remove(self.tempfile)
            except OSError:
                pass
        return

#############################################################################################
# COMMAND-LINE OPTIONS
#############################################################################################
def setupOptionParser():
    parser = OptionParser(add_help_option=False, description=DESCRIPTION)
    parser.add_option("-h", "--dbhost", dest="dbhost", help="DB Host Name or IP", default="", metavar="DBHOST")
    parser.add_option("-p", "--port", dest="dbport", help="DB host port", default="5432", metavar="DBPORT")
    parser.add_option("-U", "--dbuser", dest="dbuser", help="DB host user", default="", metavar="DBUSER")
    parser.add_option("-d", "--database", dest="database", help="Database name", default="", metavar="DATABASE")

    parser.add_option("-w", "--waitslocks", dest="waitslocks", type="int", help="Wait/lock threshold in seconds", default=-999, metavar="SECONDS")
    parser.add_option("-l", "--longquerymins", dest="longquerymins", type="int", help="Long query threshold in minutes", default=-999, metavar="MINUTES")
    parser.add_option("-c", "--vcpus", dest="vcpus", type="int", help="Available CPU count", default=-999, metavar="VCPUS")
    parser.add_option("-i", "--idleintransmins", dest="idleintransmins", type="int", help="Idle-in-transaction threshold in minutes", default=-999, metavar="MINUTES")
    parser.add_option("-o", "--idleconnmins", dest="idleconnmins", type="int", help="Idle connection threshold in minutes", default=DEFAULT_MAX_IDLE_AGE_MINS, metavar="MINUTES")
    parser.add_option("--html", dest="html_format", help="HTML report format", default=False, action="store_true")

    parser.add_option("-v", "--verbose", dest="verbose", help="Verbose output", default=False, action="store_true")
    parser.add_option("-b", "--debug", dest="debug", help="Debug output", default=False, action="store_true")

    return parser

#############################################################################################
# MAIN ENTRY POINT
#############################################################################################
def main():
    option_parser = setupOptionParser()
    options, args = option_parser.parse_args()

    pg = maint()
    rc, errors = pg.set_dbinfo(options, sys.argv)
    if rc != SUCCESS:
        print(errors)
        option_parser.print_help()
        pg.cleanup()
        return 1

    print("[INFO]  %s  version: %.1f  %s     Python Version: %d     PG Database: %s" % (PROGNAME, VERSION, ADATE, sys.version_info[0], pg.database))
    rc, results = pg.run_checks()
    if rc != SUCCESS:
        print("ERROR: %s" % results)
        pg.cleanup()
        return 1

    if pg.html_format:
        rc, message = pg.generate_html_report(results)

        if rc != SUCCESS:
            print("ERROR: %s" % message)
            pg.cleanup()
            return 1

        print(MARK_INFO + message)

    pg.cleanup()
    return 0    

if __name__ == "__main__":
    sys.exit(main())
