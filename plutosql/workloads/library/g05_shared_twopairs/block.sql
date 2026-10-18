-- Üretilmiş iş yükü: g05_shared_twopairs
-- İki ortak referans: R1 ve R2 iki aşamada da aranıyor (iki kuplajlı çift)
DECLARE
  CURSOR c_s1 IS
    SELECT id, grp, k1, k2, amount
      FROM G05_SHARED_TWOPAIRS_SRC;
  r_s1 c_s1%ROWTYPE;
  l_s1_v1 G05_SHARED_TWOPAIRS_R1.v%TYPE;
  l_s1_v2 G05_SHARED_TWOPAIRS_R2.v%TYPE;
  l_s1_amt NUMBER;
  CURSOR c_s2 IS
    SELECT id, grp, k1, k2, amount
      FROM G05_SHARED_TWOPAIRS_T1
     WHERE MOD(id, 100) < 60;
  r_s2 c_s2%ROWTYPE;
  l_s2_v1 G05_SHARED_TWOPAIRS_R1.v%TYPE;
  l_s2_v2 G05_SHARED_TWOPAIRS_R2.v%TYPE;
  l_s2_amt NUMBER;
  l_rows PLS_INTEGER := 0;
BEGIN
  -- Aşama 1: G05_SHARED_TWOPAIRS_SRC → G05_SHARED_TWOPAIRS_T1 (INSERT, arama: G05_SHARED_TWOPAIRS_R1, G05_SHARED_TWOPAIRS_R2)
  OPEN c_s1;
  LOOP
    FETCH c_s1 INTO r_s1;
    EXIT WHEN c_s1%NOTFOUND;

    SELECT v INTO l_s1_v1 FROM G05_SHARED_TWOPAIRS_R1 WHERE k = r_s1.k1;
    SELECT v INTO l_s1_v2 FROM G05_SHARED_TWOPAIRS_R2 WHERE k = r_s1.k2;
    l_s1_amt := ROUND(r_s1.amount * l_s1_v1 * l_s1_v2, 2);

    INSERT INTO G05_SHARED_TWOPAIRS_T1 (id, grp, k1, k2, amount)
    VALUES (r_s1.id, r_s1.grp, r_s1.k1, r_s1.k2, l_s1_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s1;

  -- Aşama 2: G05_SHARED_TWOPAIRS_T1 → G05_SHARED_TWOPAIRS_T2 (INSERT, arama: G05_SHARED_TWOPAIRS_R1, G05_SHARED_TWOPAIRS_R2)
  OPEN c_s2;
  LOOP
    FETCH c_s2 INTO r_s2;
    EXIT WHEN c_s2%NOTFOUND;

    SELECT v INTO l_s2_v1 FROM G05_SHARED_TWOPAIRS_R1 WHERE k = r_s2.k1;
    SELECT v INTO l_s2_v2 FROM G05_SHARED_TWOPAIRS_R2 WHERE k = r_s2.k2;
    l_s2_amt := ROUND(r_s2.amount * l_s2_v1 * l_s2_v2, 2);

    INSERT INTO G05_SHARED_TWOPAIRS_T2 (id, grp, k1, k2, amount)
    VALUES (r_s2.id, r_s2.grp, r_s2.k1, r_s2.k2, l_s2_amt);
    l_rows := l_rows + 1;
  END LOOP;
  CLOSE c_s2;
  COMMIT;
END;
/
