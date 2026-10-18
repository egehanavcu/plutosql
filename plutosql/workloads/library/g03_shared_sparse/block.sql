-- Üretilmiş iş yükü: g03_shared_sparse
-- Ortak büyük seyrek referans + 2. aşamada küçük referans, ardından upsert
DECLARE
  CURSOR c_s1 IS
    SELECT id, grp, k1, k2, amount
      FROM G03_SHARED_SPARSE_SRC;
  r_s1 c_s1%ROWTYPE;
  l_s1_v1 G03_SHARED_SPARSE_R1.v%TYPE;
  l_s1_amt NUMBER;
  CURSOR c_s2 IS
    SELECT id, grp, k1, k2, amount
      FROM G03_SHARED_SPARSE_T1
     WHERE MOD(id, 100) < 50;
  r_s2 c_s2%ROWTYPE;
  l_s2_v1 G03_SHARED_SPARSE_R1.v%TYPE;
  l_s2_v2 G03_SHARED_SPARSE_R2.v%TYPE;
  l_s2_amt NUMBER;
  CURSOR c_s3 IS
    SELECT grp, SUM(amount) AS total, COUNT(*) AS cnt
      FROM G03_SHARED_SPARSE_T2
     GROUP BY grp;
  r_s3 c_s3%ROWTYPE;
  l_rows PLS_INTEGER := 0;
BEGIN
  -- Aşama 1: G03_SHARED_SPARSE_SRC → G03_SHARED_SPARSE_T1 (INSERT, arama: G03_SHARED_SPARSE_R1)
  OPEN c_s1;
  LOOP
    FETCH c_s1 INTO r_s1;
    EXIT WHEN c_s1%NOTFOUND;

    SELECT v INTO l_s1_v1 FROM G03_SHARED_SPARSE_R1 WHERE k = r_s1.k1;
    l_s1_amt := ROUND(r_s1.amount * l_s1_v1, 2);

    INSERT INTO G03_SHARED_SPARSE_T1 (id, grp, k1, k2, amount)
    VALUES (r_s1.id, r_s1.grp, r_s1.k1, r_s1.k2, l_s1_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s1;

  -- Aşama 2: G03_SHARED_SPARSE_T1 → G03_SHARED_SPARSE_T2 (INSERT, arama: G03_SHARED_SPARSE_R1, G03_SHARED_SPARSE_R2)
  OPEN c_s2;
  LOOP
    FETCH c_s2 INTO r_s2;
    EXIT WHEN c_s2%NOTFOUND;

    SELECT v INTO l_s2_v1 FROM G03_SHARED_SPARSE_R1 WHERE k = r_s2.k1;
    SELECT v INTO l_s2_v2 FROM G03_SHARED_SPARSE_R2 WHERE k = r_s2.k2;
    l_s2_amt := ROUND(r_s2.amount * l_s2_v1 * l_s2_v2, 2);

    INSERT INTO G03_SHARED_SPARSE_T2 (id, grp, k1, k2, amount)
    VALUES (r_s2.id, r_s2.grp, r_s2.k1, r_s2.k2, l_s2_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s2;

  -- Aşama 3: G03_SHARED_SPARSE_T2 → G03_SHARED_SPARSE_T3 (UPSERT)
  OPEN c_s3;
  LOOP
    FETCH c_s3 INTO r_s3;
    EXIT WHEN c_s3%NOTFOUND;

    UPDATE G03_SHARED_SPARSE_T3 t
       SET t.total = t.total + r_s3.total, t.cnt = t.cnt + r_s3.cnt
     WHERE t.grp = r_s3.grp;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO G03_SHARED_SPARSE_T3 (grp, total, cnt)
      VALUES (r_s3.grp, r_s3.total, r_s3.cnt);
    END IF;
  END LOOP;
  CLOSE c_s3;
  COMMIT;
END;
/
