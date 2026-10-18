-- Üretilmiş iş yükü: g06_three_refs
-- Üç aşama, üç farklı referans: küçük / orta / büyük seyrek (ortak yok)
DECLARE
  CURSOR c_s1 IS
    SELECT id, grp, k1, k2, k3, amount
      FROM G06_THREE_REFS_SRC;
  r_s1 c_s1%ROWTYPE;
  l_s1_v1 G06_THREE_REFS_R1.v%TYPE;
  l_s1_amt NUMBER;
  CURSOR c_s2 IS
    SELECT id, grp, k1, k2, k3, amount
      FROM G06_THREE_REFS_T1;
  r_s2 c_s2%ROWTYPE;
  l_s2_v2 G06_THREE_REFS_R2.v%TYPE;
  l_s2_amt NUMBER;
  CURSOR c_s3 IS
    SELECT id, grp, k1, k2, k3, amount
      FROM G06_THREE_REFS_T2;
  r_s3 c_s3%ROWTYPE;
  l_s3_v3 G06_THREE_REFS_R3.v%TYPE;
  l_s3_amt NUMBER;
  l_rows PLS_INTEGER := 0;
BEGIN
  -- Aşama 1: G06_THREE_REFS_SRC → G06_THREE_REFS_T1 (INSERT, arama: G06_THREE_REFS_R1)
  OPEN c_s1;
  LOOP
    FETCH c_s1 INTO r_s1;
    EXIT WHEN c_s1%NOTFOUND;

    SELECT v INTO l_s1_v1 FROM G06_THREE_REFS_R1 WHERE k = r_s1.k1;
    l_s1_amt := ROUND(r_s1.amount * l_s1_v1, 2);

    INSERT INTO G06_THREE_REFS_T1 (id, grp, k1, k2, k3, amount)
    VALUES (r_s1.id, r_s1.grp, r_s1.k1, r_s1.k2, r_s1.k3, l_s1_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s1;

  -- Aşama 2: G06_THREE_REFS_T1 → G06_THREE_REFS_T2 (INSERT, arama: G06_THREE_REFS_R2)
  OPEN c_s2;
  LOOP
    FETCH c_s2 INTO r_s2;
    EXIT WHEN c_s2%NOTFOUND;

    SELECT v INTO l_s2_v2 FROM G06_THREE_REFS_R2 WHERE k = r_s2.k2;
    l_s2_amt := ROUND(r_s2.amount * l_s2_v2, 2);

    INSERT INTO G06_THREE_REFS_T2 (id, grp, k1, k2, k3, amount)
    VALUES (r_s2.id, r_s2.grp, r_s2.k1, r_s2.k2, r_s2.k3, l_s2_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s2;

  -- Aşama 3: G06_THREE_REFS_T2 → G06_THREE_REFS_T3 (INSERT, arama: G06_THREE_REFS_R3)
  OPEN c_s3;
  LOOP
    FETCH c_s3 INTO r_s3;
    EXIT WHEN c_s3%NOTFOUND;

    SELECT v INTO l_s3_v3 FROM G06_THREE_REFS_R3 WHERE k = r_s3.k3;
    l_s3_amt := ROUND(r_s3.amount * l_s3_v3, 2);

    INSERT INTO G06_THREE_REFS_T3 (id, grp, k1, k2, k3, amount)
    VALUES (r_s3.id, r_s3.grp, r_s3.k1, r_s3.k2, r_s3.k3, l_s3_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s3;
  COMMIT;
END;
/
