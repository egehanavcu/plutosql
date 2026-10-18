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
    TYPE px_s2_val_t IS RECORD (rate ref_fx_rate.rate%TYPE);
    TYPE px_s2_map_t IS TABLE OF px_s2_val_t INDEX BY VARCHAR2(3);
    px_s2_map px_s2_map_t;
    TYPE px_s2_dup_t IS TABLE OF BOOLEAN INDEX BY VARCHAR2(3);
    px_s2_dup px_s2_dup_t;
  BEGIN
    FOR px_s2_r IN (SELECT currency AS px_k, rate AS px_v1
                      FROM ref_fx_rate
                     WHERE currency IS NOT NULL) LOOP
      IF px_s2_map.EXISTS(px_s2_r.px_k) THEN
        px_s2_dup(px_s2_r.px_k) := TRUE;
      ELSE
        px_s2_map(px_s2_r.px_k).rate := px_s2_r.px_v1;
      END IF;
    END LOOP;
    OPEN c_stg;
    LOOP
      FETCH c_stg BULK COLLECT INTO px_s1_rows LIMIT 500;
      EXIT WHEN px_s1_rows.COUNT = 0;
      FOR px_s1_i IN 1 .. px_s1_rows.COUNT LOOP
        IF px_s1_rows(px_s1_i).currency IS NULL OR LENGTHB(px_s1_rows(px_s1_i).currency) > 3 OR NOT px_s2_map.EXISTS(px_s1_rows(px_s1_i).currency) THEN
          RAISE NO_DATA_FOUND;
        END IF;
        IF px_s2_dup.EXISTS(px_s1_rows(px_s1_i).currency) THEN
          RAISE TOO_MANY_ROWS;
        END IF;
        l_rate := px_s2_map(px_s1_rows(px_s1_i).currency).rate;
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
  DECLARE
    TYPE px_s3_rows_t IS TABLE OF c_enr%ROWTYPE;
    px_s3_rows px_s3_rows_t;
    TYPE px_s3_ins_t IS TABLE OF PLS_INTEGER INDEX BY PLS_INTEGER;
    px_s3_ins px_s3_ins_t;
  BEGIN
    OPEN c_enr;
    LOOP
      FETCH c_enr BULK COLLECT INTO px_s3_rows LIMIT 50;
      EXIT WHEN px_s3_rows.COUNT = 0;
      FORALL px_s3_i IN 1 .. px_s3_rows.COUNT
        UPDATE settlement s
           SET s.total_amount = s.total_amount + px_s3_rows(px_s3_i).total
         WHERE s.account_id = px_s3_rows(px_s3_i).account_id
           AND s.settle_date = px_s3_rows(px_s3_i).settle_date;
      FOR px_s3_i IN 1 .. px_s3_rows.COUNT LOOP
        IF SQL%BULK_ROWCOUNT(px_s3_i) = 0 THEN
          px_s3_ins(px_s3_ins.COUNT + 1) := px_s3_i;
        END IF;
      END LOOP;
      FORALL px_s3_i IN VALUES OF px_s3_ins
        INSERT INTO settlement (account_id, settle_date, total_amount)
        VALUES (px_s3_rows(px_s3_i).account_id, px_s3_rows(px_s3_i).settle_date, px_s3_rows(px_s3_i).total);
      px_s3_ins.DELETE;
    END LOOP;
    CLOSE c_enr;
  END;

  UPDATE stg_txn SET status = 'DONE' WHERE status = 'NEW';
  COMMIT;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    ROLLBACK;
    RAISE;
END;
/
