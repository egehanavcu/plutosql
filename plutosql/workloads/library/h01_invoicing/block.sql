-- Fatura kesimi (gece işi)
-- Onaylanmış sipariş satırlarından fatura satırı üretir: ürün liste fiyatı, müşteri iskontosu ve
-- kategori KDV oranı satır bazında okunur. Ardından müşteri açık bakiyeleri güncellenir.
DECLARE
  CURSOR c_lines IS
    SELECT order_id, line_no, customer_id, product_id, category, qty
      FROM h01_order_lines
     WHERE status = 'CONFIRMED';
  r_line        c_lines%ROWTYPE;
  l_price       h01_products.list_price%TYPE;
  l_discount    h01_customers.discount_pct%TYPE;
  l_vat         h01_tax_rates.vat_pct%TYPE;
  l_net         NUMBER;
  l_lines_done  PLS_INTEGER := 0;

  CURSOR c_totals IS
    SELECT customer_id, SUM(net_amount + vat_amount) AS gross, COUNT(*) AS n_lines
      FROM h01_invoice_lines
     GROUP BY customer_id;
  r_tot         c_totals%ROWTYPE;
BEGIN
  -- 1) fatura satırları
  OPEN c_lines;
  LOOP
    FETCH c_lines INTO r_line;
    EXIT WHEN c_lines%NOTFOUND;

    SELECT list_price INTO l_price FROM h01_products WHERE product_id = r_line.product_id;
    SELECT discount_pct INTO l_discount FROM h01_customers WHERE customer_id = r_line.customer_id;
    SELECT vat_pct INTO l_vat FROM h01_tax_rates WHERE category = r_line.category;

    l_net := ROUND(r_line.qty * l_price * (100 - NVL(l_discount, 0)) / 100, 2);

    INSERT INTO h01_invoice_lines (order_id, line_no, customer_id, net_amount, vat_amount)
    VALUES (r_line.order_id, r_line.line_no, r_line.customer_id, l_net, ROUND(l_net * l_vat / 100, 2));

    l_lines_done := l_lines_done + 1;
  END LOOP;
  CLOSE c_lines;

  -- 2) müşteri açık bakiyeleri
  OPEN c_totals;
  LOOP
    FETCH c_totals INTO r_tot;
    EXIT WHEN c_totals%NOTFOUND;

    UPDATE h01_customer_balance b
       SET b.open_amount = b.open_amount + r_tot.gross,
           b.invoice_count = b.invoice_count + r_tot.n_lines
     WHERE b.customer_id = r_tot.customer_id;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO h01_customer_balance (customer_id, open_amount, invoice_count)
      VALUES (r_tot.customer_id, r_tot.gross, r_tot.n_lines);
    END IF;
  END LOOP;
  CLOSE c_totals;

  COMMIT;
END;
/
