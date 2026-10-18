-- Sadakat puanı hesaplama
-- Onaylanmış POS işlemleri için işyeri bonus oranı ve sektör (MCC) kampanya çarpanı okunur,
-- işlem başına puan olayı yazılır; ardından müşteri puan bakiyeleri güncellenir.
DECLARE
  CURSOR c_txn IS
    SELECT txn_id, customer_id, merchant_id, mcc, amount
      FROM h04_pos_txn
     WHERE status = 'APPROVED';
  r_txn         c_txn%ROWTYPE;
  l_bonus       h04_merchants.bonus_pct%TYPE;
  l_mult        h04_campaigns.multiplier%TYPE;
  l_points      NUMBER;

  CURSOR c_cust IS
    SELECT customer_id, SUM(points) AS earned, COUNT(*) AS n_events
      FROM h04_point_events
     GROUP BY customer_id;
  r_cust        c_cust%ROWTYPE;
BEGIN
  -- 1) işlem başına puan
  OPEN c_txn;
  LOOP
    FETCH c_txn INTO r_txn;
    EXIT WHEN c_txn%NOTFOUND;

    SELECT bonus_pct INTO l_bonus FROM h04_merchants WHERE merchant_id = r_txn.merchant_id;
    SELECT multiplier INTO l_mult FROM h04_campaigns WHERE mcc = r_txn.mcc;

    l_points := FLOOR(r_txn.amount * l_mult * (100 + l_bonus) / 1000);

    INSERT INTO h04_point_events (txn_id, customer_id, points)
    VALUES (r_txn.txn_id, r_txn.customer_id, l_points);
  END LOOP;
  CLOSE c_txn;

  -- 2) müşteri bakiyeleri
  OPEN c_cust;
  LOOP
    FETCH c_cust INTO r_cust;
    EXIT WHEN c_cust%NOTFOUND;

    UPDATE h04_loyalty_balance lb
       SET lb.points = lb.points + r_cust.earned,
           lb.events = lb.events + r_cust.n_events
     WHERE lb.customer_id = r_cust.customer_id;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO h04_loyalty_balance (customer_id, points, events)
      VALUES (r_cust.customer_id, r_cust.earned, r_cust.n_events);
    END IF;
  END LOOP;
  CLOSE c_cust;

  COMMIT;
END;
/
