-- h01_invoicing: deterministik veri (ORA_HASH). 20 000 ürün, 5 000 müşteri, 12 kategori, 30 000 sipariş satırı.
-- Ürün kategorisi ürün numarasından türetilir; sipariş satırı aynı ifadeyle kategori anlık görüntüsünü taşır.
INSERT /*+ APPEND */ INTO H01_TAX_RATES (CATEGORY, VAT_PCT)
SELECT 'CAT' || LPAD(LEVEL, 2, '0'), CASE MOD(LEVEL, 3) WHEN 0 THEN 1 WHEN 1 THEN 10 ELSE 20 END
  FROM dual CONNECT BY LEVEL <= 12
/
COMMIT
/
INSERT /*+ APPEND */ INTO H01_PRODUCTS (PRODUCT_ID, CATEGORY, LIST_PRICE, DESCR)
SELECT LEVEL,
       'CAT' || LPAD(1 + MOD(ORA_HASH(LEVEL, 4294967295, 101), 12), 2, '0'),
       ROUND(1 + MOD(ORA_HASH(LEVEL, 4294967295, 102), 100000) / 100, 2),
       RPAD('urun ' || LEVEL, 120, '.')
  FROM dual CONNECT BY LEVEL <= 20000
/
COMMIT
/
INSERT /*+ APPEND */ INTO H01_CUSTOMERS (CUSTOMER_ID, DISCOUNT_PCT, SEGMENT, ADDRESS)
SELECT LEVEL,
       CASE WHEN MOD(ORA_HASH(LEVEL, 4294967295, 111), 4) = 0 THEN NULL
            ELSE MOD(ORA_HASH(LEVEL, 4294967295, 112), 2501) / 100 END,
       CASE MOD(LEVEL, 3) WHEN 0 THEN 'KURUMSAL' WHEN 1 THEN 'KOBI' ELSE 'BIREYSEL' END,
       RPAD('adres ' || LEVEL, 200, '.')
  FROM dual CONNECT BY LEVEL <= 5000
/
COMMIT
/
INSERT /*+ APPEND */ INTO H01_ORDER_LINES (ORDER_ID, LINE_NO, CUSTOMER_ID, PRODUCT_ID, CATEGORY, QTY, STATUS)
SELECT order_id, line_no, customer_id, product_id,
       'CAT' || LPAD(1 + MOD(ORA_HASH(product_id, 4294967295, 101), 12), 2, '0'),
       qty, status
  FROM (SELECT TRUNC((LEVEL - 1) / 3) + 1 AS order_id,
               MOD(LEVEL - 1, 3) + 1 AS line_no,
               1 + MOD(ORA_HASH(TRUNC((LEVEL - 1) / 3), 4294967295, 121), 5000) AS customer_id,
               1 + MOD(ORA_HASH(LEVEL, 4294967295, 122), 20000) AS product_id,
               1 + MOD(ORA_HASH(LEVEL, 4294967295, 123), 20) AS qty,
               CASE WHEN MOD(ORA_HASH(LEVEL, 4294967295, 124), 10) < 9 THEN 'CONFIRMED' ELSE 'DRAFT' END AS status
          FROM dual CONNECT BY LEVEL <= 30000)
 ORDER BY order_id, line_no
/
COMMIT
/
-- Müşterilerin yaklaşık yarısının önceden açık bakiyesi var (upsert'in iki yolu da çalışır).
INSERT /*+ APPEND */ INTO H01_CUSTOMER_BALANCE_SEED (CUSTOMER_ID, OPEN_AMOUNT, INVOICE_COUNT)
SELECT LEVEL, ROUND(MOD(ORA_HASH(LEVEL, 4294967295, 131), 5000000) / 100, 2), MOD(ORA_HASH(LEVEL, 4294967295, 132), 40)
  FROM dual
 WHERE MOD(ORA_HASH(LEVEL, 4294967295, 133), 2) = 0
CONNECT BY LEVEL <= 5000
/
COMMIT
/
