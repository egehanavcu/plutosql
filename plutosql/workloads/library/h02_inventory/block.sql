-- Stok yönetimi (gün sonu)
-- 1) Günün stok hareketleri maliyetlendirilir (ürün birim maliyeti + depo elleçleme payı).
-- 2) Depo/ürün bazında stok seviyeleri ve değerleri güncellenir.
-- 3) Asgari seviyenin altına düşen kalemler için tedarikçiye yeniden sipariş önerisi hazırlanır.
DECLARE
  CURSOR c_moves IS
    SELECT movement_id, warehouse_id, item_id, qty
      FROM h02_movements;
  r_mv          c_moves%ROWTYPE;
  l_unit_cost   h02_items.unit_cost%TYPE;
  l_handling    h02_warehouses.handling_pct%TYPE;

  CURSOR c_net IS
    SELECT warehouse_id, item_id, SUM(qty) AS net_qty, SUM(cost_amount) AS net_value
      FROM h02_movement_costed
     GROUP BY warehouse_id, item_id;
  r_net         c_net%ROWTYPE;

  CURSOR c_short IS
    SELECT warehouse_id, item_id, on_hand, min_qty
      FROM h02_stock_levels
     WHERE on_hand < min_qty;
  r_sh          c_short%ROWTYPE;
  l_supplier    h02_items.supplier_id%TYPE;
  l_cost        h02_items.unit_cost%TYPE;
  l_order_qty   NUMBER;
  l_proposals   PLS_INTEGER := 0;
BEGIN
  -- 1) hareket maliyetlendirme
  OPEN c_moves;
  LOOP
    FETCH c_moves INTO r_mv;
    EXIT WHEN c_moves%NOTFOUND;

    SELECT unit_cost INTO l_unit_cost FROM h02_items WHERE item_id = r_mv.item_id;
    SELECT handling_pct INTO l_handling FROM h02_warehouses WHERE warehouse_id = r_mv.warehouse_id;

    INSERT INTO h02_movement_costed (movement_id, warehouse_id, item_id, qty, cost_amount)
    VALUES (r_mv.movement_id, r_mv.warehouse_id, r_mv.item_id, r_mv.qty,
            ROUND(r_mv.qty * l_unit_cost * (100 + l_handling) / 100, 4));
  END LOOP;
  CLOSE c_moves;

  -- 2) stok seviyeleri
  OPEN c_net;
  LOOP
    FETCH c_net INTO r_net;
    EXIT WHEN c_net%NOTFOUND;

    UPDATE h02_stock_levels s
       SET s.on_hand = s.on_hand + r_net.net_qty,
           s.stock_value = s.stock_value + r_net.net_value
     WHERE s.warehouse_id = r_net.warehouse_id
       AND s.item_id = r_net.item_id;

    IF SQL%ROWCOUNT = 0 THEN
      INSERT INTO h02_stock_levels (warehouse_id, item_id, on_hand, stock_value, min_qty)
      VALUES (r_net.warehouse_id, r_net.item_id, r_net.net_qty, r_net.net_value, 10);
    END IF;
  END LOOP;
  CLOSE c_net;

  -- 3) yeniden sipariş önerileri
  OPEN c_short;
  LOOP
    FETCH c_short INTO r_sh;
    EXIT WHEN c_short%NOTFOUND;

    SELECT supplier_id, unit_cost INTO l_supplier, l_cost FROM h02_items WHERE item_id = r_sh.item_id;
    l_order_qty := 2 * r_sh.min_qty - r_sh.on_hand;

    INSERT INTO h02_reorder_proposals (warehouse_id, item_id, supplier_id, order_qty, est_cost)
    VALUES (r_sh.warehouse_id, r_sh.item_id, l_supplier, l_order_qty, ROUND(l_order_qty * l_cost, 4));

    l_proposals := l_proposals + 1;
  END LOOP;
  CLOSE c_short;

  COMMIT;
END;
/
