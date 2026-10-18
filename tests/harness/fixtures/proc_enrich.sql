-- Prosedür iş yükü (OUT parametresi doğrulaması için): yalnızca zenginleştirme aşaması.
CREATE OR REPLACE PROCEDURE pluto_enrich (
  p_status IN  VARCHAR2,
  p_rows   OUT NUMBER
) AS
  CURSOR c_stg IS
    SELECT txn_id, account_id, currency, amount, txn_date
      FROM stg_txn
     WHERE status = p_status;
  r_stg  c_stg%ROWTYPE;
  l_rate ref_fx_rate.rate%TYPE;
  l_n    PLS_INTEGER := 0;
BEGIN
  OPEN c_stg;
  LOOP
    FETCH c_stg INTO r_stg;
    EXIT WHEN c_stg%NOTFOUND;

    SELECT rate INTO l_rate FROM ref_fx_rate WHERE currency = r_stg.currency;

    INSERT INTO txn_enriched (txn_id, account_id, txn_date, amount_try)
    VALUES (r_stg.txn_id, r_stg.account_id, r_stg.txn_date, r_stg.amount * l_rate);

    l_n := l_n + 1;
  END LOOP;
  CLOSE c_stg;
  p_rows := l_n;
END pluto_enrich;
/
