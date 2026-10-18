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
    TYPE px_s1_rows_t IS TABLE OF c_stg%ROWTYPE;
    px_s1_rows px_s1_rows_t;
    TYPE px_s1_l_rate_t IS TABLE OF l_rate%TYPE INDEX BY PLS_INTEGER;
    px_s1_l_rate px_s1_l_rate_t;
  BEGIN
    OPEN c_stg;
    LOOP
      FETCH c_stg BULK COLLECT INTO px_s1_rows LIMIT 50;
      EXIT WHEN px_s1_rows.COUNT = 0;
      FOR px_s1_i IN 1 .. px_s1_rows.COUNT LOOP
        SELECT rate INTO l_rate
          FROM ref_fx_rate
         WHERE currency = px_s1_rows(px_s1_i).currency;
        px_s1_l_rate(px_s1_i) := l_rate;
      END LOOP;
      FORALL px_s1_i IN 1 .. px_s1_rows.COUNT
        INSERT INTO txn_enriched (txn_id, account_id, txn_date, amount_try)
        VALUES (px_s1_rows(px_s1_i).txn_id, px_s1_rows(px_s1_i).account_id, px_s1_rows(px_s1_i).txn_date, px_s1_rows(px_s1_i).amount * px_s1_l_rate(px_s1_i));
      l_processed := l_processed + px_s1_rows.COUNT;
    END LOOP;
    CLOSE c_stg;
  END;

  /* Aşama 3: mutabakat (satır bazlı upsert) */
  OPEN c_enr;
  LOOP
    FETCH c_enr INTO r_enr;
    EXIT WHEN c_enr%NOTFOUND;

    UPDATE settlement s
       SET s.total_amount = s.total_amount + r_enr.total
     WHERE s.account_id = r_enr.account_id
       AND s.settle_date = r_enr.settle_date;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO settlement (account_id, settle_date, total_amount)
      VALUES (r_enr.account_id, r_enr.settle_date, r_enr.total);
    END IF;
  END LOOP;
  CLOSE c_enr;

  UPDATE stg_txn SET status = 'DONE' WHERE status = 'NEW';
  COMMIT;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    ROLLBACK;
    RAISE;
END;
/
