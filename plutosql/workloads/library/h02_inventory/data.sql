-- h02_inventory: deterministik veri (ORA_HASH). 30 000 ürün kartı, 40 depo, 40 000 hareket
-- (hareketler 8 000 farklı ürüne yoğunlaşır: depo/ürün toplamları tekrarlı olur).
INSERT /*+ APPEND */ INTO H02_WAREHOUSES (WAREHOUSE_ID, REGION, HANDLING_PCT)
SELECT LEVEL, 'BLG' || MOD(LEVEL, 7), ROUND(1 + MOD(ORA_HASH(LEVEL, 4294967295, 201), 900) / 100, 2)
  FROM dual CONNECT BY LEVEL <= 40
/
COMMIT
/
INSERT /*+ APPEND */ INTO H02_ITEMS (ITEM_ID, UNIT_COST, SUPPLIER_ID, DESCR)
SELECT LEVEL,
       ROUND(0.5 + MOD(ORA_HASH(LEVEL, 4294967295, 211), 500000) / 1000, 4),
       1 + MOD(ORA_HASH(LEVEL, 4294967295, 212), 600),
       RPAD('stok kalemi ' || LEVEL, 150, '.')
  FROM dual CONNECT BY LEVEL <= 30000
/
COMMIT
/
INSERT /*+ APPEND */ INTO H02_MOVEMENTS (MOVEMENT_ID, WAREHOUSE_ID, ITEM_ID, QTY, MOVED_AT)
SELECT LEVEL,
       1 + MOD(ORA_HASH(LEVEL, 4294967295, 221), 40),
       1 + MOD(ORA_HASH(LEVEL, 4294967295, 222), 8000),
       MOD(ORA_HASH(LEVEL, 4294967295, 223), 41) - 15,
       DATE '2024-03-01' + MOD(ORA_HASH(LEVEL, 4294967295, 224), 86400) / 86400
  FROM dual CONNECT BY LEVEL <= 40000
 ORDER BY 1
/
COMMIT
/
-- Mevcut stok: hareket gören depo/ürün çiftlerinin yaklaşık yarısı ve hiç hareket görmeyen bazı kalemler.
INSERT /*+ APPEND */ INTO H02_STOCK_LEVELS_SEED (WAREHOUSE_ID, ITEM_ID, ON_HAND, STOCK_VALUE, MIN_QTY)
SELECT warehouse_id, item_id,
       MOD(ORA_HASH(warehouse_id * 100000 + item_id, 4294967295, 231), 60),
       ROUND(MOD(ORA_HASH(warehouse_id * 100000 + item_id, 4294967295, 232), 1000000) / 100, 4),
       5 + MOD(ORA_HASH(warehouse_id * 100000 + item_id, 4294967295, 233), 26)
  FROM (SELECT DISTINCT warehouse_id, item_id FROM H02_MOVEMENTS
        UNION
        SELECT 1 + MOD(LEVEL, 40), 8000 + LEVEL FROM dual CONNECT BY LEVEL <= 2000)
 WHERE MOD(ORA_HASH(warehouse_id * 100000 + item_id, 4294967295, 234), 2) = 0
 ORDER BY warehouse_id, item_id
/
COMMIT
/
