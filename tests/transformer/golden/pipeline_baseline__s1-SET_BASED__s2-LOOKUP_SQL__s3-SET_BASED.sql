-- Referans boru hattı (baseline): Staging -> Transformation -> Settlement
-- ADR-001: satır bazlı döngüler açık imleç (OPEN/FETCH/EXIT WHEN %NOTFOUND) kalıbıyla yazılır.
DECLARE
  CURSOR c_stg IS
    SELECT txn_id, account_id, currency, amount, txn_date
      FROM stg_txn
     WHERE status = 'NEW';
  r_stg       c_stg%ROWTYPE;
  l_rate      ref_fx_rate.rate%TYPE;

  CURSOR c_enr IS
    SELECT account_id, TRUNC(txn_date) AS settle_date, SUM(amount_try) AS total
      FROM txn_enriched
     GROUP BY account_id, TRUNC(txn_date);
  r_enr       c_enr%ROWTYPE;
  l_processed PLS_INTEGER := 0;
BEGIN
  -- Aşama 1-2: zenginleştirme (döngü içi referans araması)
  DECLARE
    px_s1_miss PLS_INTEGER;
    px_s1_tmr EXCEPTION;
    PRAGMA EXCEPTION_INIT(px_s1_tmr, -1427);
  BEGIN
    SELECT COUNT(*) INTO px_s1_miss
      FROM (
          SELECT txn_id, account_id, currency, amount, txn_date
            FROM stg_txn
           WHERE status = 'NEW'
           ) px_src
     WHERE NOT EXISTS (SELECT 1 FROM ref_fx_rate px_l1 WHERE px_l1.currency = px_src.currency);
    IF px_s1_miss > 0 THEN
      RAISE NO_DATA_FOUND;
    END IF;
    INSERT INTO txn_enriched (txn_id, account_id, txn_date, amount_try)
    SELECT px_src.txn_id,
           px_src.account_id,
           px_src.txn_date,
           px_src.amount * (SELECT px_l1.rate FROM ref_fx_rate px_l1 WHERE px_l1.currency = px_src.currency)
      FROM (
          SELECT txn_id, account_id, currency, amount, txn_date
            FROM stg_txn
           WHERE status = 'NEW'
           ) px_src;
    l_processed := l_processed + SQL%ROWCOUNT;
  EXCEPTION
    WHEN px_s1_tmr THEN
      RAISE TOO_MANY_ROWS;
  END;

  /* Aşama 3: mutabakat (satır bazlı upsert) */
  MERGE INTO settlement s
  USING (
        SELECT account_id, TRUNC(txn_date) AS settle_date, SUM(amount_try) AS total
          FROM txn_enriched
         GROUP BY account_id, TRUNC(txn_date)
        ) px_src
     ON (s.account_id = px_src.account_id
         AND s.settle_date = px_src.settle_date)
   WHEN MATCHED THEN
     UPDATE SET s.total_amount = s.total_amount + px_src.total
   WHEN NOT MATCHED THEN
     INSERT (account_id, settle_date, total_amount)
     VALUES (px_src.account_id, px_src.settle_date, px_src.total);

  UPDATE stg_txn SET status = 'DONE' WHERE status = 'NEW';
  COMMIT;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    ROLLBACK;
    RAISE;
END;
/
