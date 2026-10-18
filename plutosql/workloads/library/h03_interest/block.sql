-- Günlük faiz tahakkuku
-- Aktif ve pozitif bakiyeli mevduat hesapları için ürün faiz oranı ve şube stopaj oranı okunur,
-- brüt/stopaj/net günlük faiz tahakkuk kaydı yazılır; ardından hesapların birikmiş faizi güncellenir.
DECLARE
  CURSOR c_acc IS
    SELECT account_id, branch_id, product_code, balance
      FROM h03_accounts
     WHERE status = 'ACTIVE'
       AND balance > 0;
  r_acc         c_acc%ROWTYPE;
  l_rate        h03_products.annual_rate%TYPE;
  l_wht         h03_branches.withholding_pct%TYPE;
  l_gross       NUMBER;
  l_tax         NUMBER;
  l_accrued_n   PLS_INTEGER := 0;

  CURSOR c_post IS
    SELECT account_id, net
      FROM h03_accruals;
  r_post        c_post%ROWTYPE;
BEGIN
  -- 1) tahakkuk
  OPEN c_acc;
  LOOP
    FETCH c_acc INTO r_acc;
    EXIT WHEN c_acc%NOTFOUND;

    SELECT annual_rate INTO l_rate FROM h03_products WHERE product_code = r_acc.product_code;
    SELECT withholding_pct INTO l_wht FROM h03_branches WHERE branch_id = r_acc.branch_id;

    l_gross := ROUND(r_acc.balance * l_rate / 100 / 365, 2);
    l_tax := ROUND(l_gross * l_wht / 100, 2);

    INSERT INTO h03_accruals (account_id, gross, tax, net)
    VALUES (r_acc.account_id, l_gross, l_tax, l_gross - l_tax);

    l_accrued_n := l_accrued_n + 1;
  END LOOP;
  CLOSE c_acc;

  -- 2) hesaplara işleme
  OPEN c_post;
  LOOP
    FETCH c_post INTO r_post;
    EXIT WHEN c_post%NOTFOUND;

    UPDATE h03_accounts a
       SET a.accrued = a.accrued + r_post.net
     WHERE a.account_id = r_post.account_id;
  END LOOP;
  CLOSE c_post;

  COMMIT;
END;
/
