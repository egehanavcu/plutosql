#!/bin/bash
# İlk veritabanı oluşturulurken bir kez çalışır (gvenzl/oracle-free initdb mekanizması).
# Deney kullanıcısını FREEPDB1 içinde oluşturur ve ölçüm için gereken V$ görünümlerini açar.
set -euo pipefail

sqlplus -s / as sysdba <<EOF
WHENEVER SQLERROR EXIT SQL.SQLCODE
ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER ${PLUTO_USER} IDENTIFIED BY "${PLUTO_PASSWORD}"
  DEFAULT TABLESPACE users
  QUOTA UNLIMITED ON users;

GRANT CREATE SESSION, ALTER SESSION,
      CREATE TABLE, CREATE VIEW, CREATE SEQUENCE, CREATE PROCEDURE
   TO ${PLUTO_USER};

-- Ölçüm: oturum istatistikleri (consistent gets, session pga memory [max])
GRANT SELECT ON sys.v_\$mystat   TO ${PLUTO_USER};
GRANT SELECT ON sys.v_\$statname TO ${PLUTO_USER};
GRANT SELECT ON sys.v_\$sesstat  TO ${PLUTO_USER};
GRANT SELECT ON sys.v_\$session  TO ${PLUTO_USER};
GRANT SELECT ON sys.v_\$process  TO ${PLUTO_USER};
-- ADR-001: oturumdaki plsql_optimize_level değeri ölçüm kaydına yazılır
GRANT SELECT ON sys.v_\$parameter TO ${PLUTO_USER};
EXIT
EOF
