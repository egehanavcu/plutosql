-- Dönüştürücünün üreteceği biçimler (BULK COLLECT/FORALL, Associative Array önbelleği, MERGE).
-- Dönüştürülmüş kod da yeniden ayrıştırılabilmelidir.
DECLARE
  CURSOR c_stg IS
    SELECT txn_id, account_id, currency, amount, txn_date FROM stg_txn WHERE status = 'NEW';
  TYPE t_stg_tab IS TABLE OF c_stg%ROWTYPE;
  l_rows       t_stg_tab;
  TYPE t_rate_cache IS TABLE OF ref_fx_rate.rate%TYPE INDEX BY ref_fx_rate.currency%TYPE;
  l_rate_cache t_rate_cache;
  TYPE t_num_tab IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
  l_amount_try t_num_tab;
BEGIN
  FOR r IN (SELECT currency, rate FROM ref_fx_rate WHERE currency IS NOT NULL) LOOP
    l_rate_cache(r.currency) := r.rate;
  END LOOP;

  OPEN c_stg;
  LOOP
    FETCH c_stg BULK COLLECT INTO l_rows LIMIT 500;
    EXIT WHEN l_rows.COUNT = 0;

    FOR i IN 1 .. l_rows.COUNT LOOP
      IF l_rows(i).currency IS NULL OR NOT l_rate_cache.EXISTS(l_rows(i).currency) THEN
        RAISE NO_DATA_FOUND;
      END IF;
      l_amount_try(i) := l_rows(i).amount * l_rate_cache(l_rows(i).currency);
    END LOOP;

    FORALL i IN 1 .. l_rows.COUNT
      INSERT INTO txn_enriched (txn_id, account_id, txn_date, amount_try)
      VALUES (l_rows(i).txn_id, l_rows(i).account_id, l_rows(i).txn_date, l_amount_try(i));
  END LOOP;
  CLOSE c_stg;

  MERGE INTO settlement s
  USING (SELECT account_id, TRUNC(txn_date) AS settle_date, SUM(amount_try) AS total
           FROM txn_enriched
          GROUP BY account_id, TRUNC(txn_date)) e
     ON (s.account_id = e.account_id AND s.settle_date = e.settle_date)
   WHEN MATCHED THEN
     UPDATE SET s.total_amount = s.total_amount + e.total
   WHEN NOT MATCHED THEN
     INSERT (account_id, settle_date, total_amount)
     VALUES (e.account_id, e.settle_date, e.total);

  l_rate_cache.DELETE;
  COMMIT;
END;
/
