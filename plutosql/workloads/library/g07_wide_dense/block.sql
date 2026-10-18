-- Üretilmiş iş yükü: g07_wide_dense
-- Geniş satırlı büyük referans, yoğun erişim; küçük referans; ardından upsert
DECLARE
  CURSOR c_s1 IS
    SELECT id, grp, k1, k2, amount
      FROM G07_WIDE_DENSE_SRC;
  r_s1 c_s1%ROWTYPE;
  l_s1_v1 G07_WIDE_DENSE_R1.v%TYPE;
  l_s1_v2 G07_WIDE_DENSE_R2.v%TYPE;
  l_s1_amt NUMBER;
  CURSOR c_s2 IS
    SELECT grp, SUM(amount) AS total, COUNT(*) AS cnt
      FROM G07_WIDE_DENSE_T1
     GROUP BY grp;
  r_s2 c_s2%ROWTYPE;
  l_rows PLS_INTEGER := 0;
BEGIN
  -- Aşama 1: G07_WIDE_DENSE_SRC → G07_WIDE_DENSE_T1 (INSERT, arama: G07_WIDE_DENSE_R1, G07_WIDE_DENSE_R2)
  OPEN c_s1;
  LOOP
    FETCH c_s1 INTO r_s1;
    EXIT WHEN c_s1%NOTFOUND;

    SELECT v INTO l_s1_v1 FROM G07_WIDE_DENSE_R1 WHERE k = r_s1.k1;
    SELECT v INTO l_s1_v2 FROM G07_WIDE_DENSE_R2 WHERE k = r_s1.k2;
    l_s1_amt := ROUND(r_s1.amount * l_s1_v1 * l_s1_v2, 2);

    INSERT INTO G07_WIDE_DENSE_T1 (id, grp, k1, k2, amount)
    VALUES (r_s1.id, r_s1.grp, r_s1.k1, r_s1.k2, l_s1_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s1;

  -- Aşama 2: G07_WIDE_DENSE_T1 → G07_WIDE_DENSE_T2 (UPSERT)
  OPEN c_s2;
  LOOP
    FETCH c_s2 INTO r_s2;
    EXIT WHEN c_s2%NOTFOUND;

    UPDATE G07_WIDE_DENSE_T2 t
       SET t.total = t.total + r_s2.total, t.cnt = t.cnt + r_s2.cnt
     WHERE t.grp = r_s2.grp;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO G07_WIDE_DENSE_T2 (grp, total, cnt)
      VALUES (r_s2.grp, r_s2.total, r_s2.cnt);
    END IF;
  END LOOP;
  CLOSE c_s2;
  COMMIT;
END;
/
