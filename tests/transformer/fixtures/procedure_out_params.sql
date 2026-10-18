CREATE OR REPLACE PROCEDURE settle_account (
  p_account_id IN     settlement.account_id%TYPE,
  p_from       IN     DATE DEFAULT DATE '2024-01-01',
  p_rows       OUT    NUMBER,
  p_log        IN OUT NOCOPY VARCHAR2
) AS
  e_no_rows EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_no_rows, -20001);
  l_total   NUMBER(18, 2) := 0;
  c_label   CONSTANT VARCHAR2(30 CHAR) := 'settle';

  FUNCTION fmt (p_val IN NUMBER) RETURN VARCHAR2 IS
  BEGIN
    RETURN TO_CHAR(p_val, 'FM999G999D00');
  END fmt;
BEGIN
  SELECT NVL(SUM(total_amount), 0), COUNT(*)
    INTO l_total, p_rows
    FROM settlement
   WHERE account_id = p_account_id
     AND settle_date >= p_from;

  IF p_rows = 0 THEN
    RAISE e_no_rows;
  ELSIF l_total < 0 THEN
    p_log := p_log || c_label || ': negatif bakiye ' || fmt(l_total);
  ELSE
    p_log := p_log || c_label || ': ' || fmt(l_total);
  END IF;
EXCEPTION
  WHEN e_no_rows OR NO_DATA_FOUND THEN
    p_rows := 0;
    raise_application_error(-20001, 'Kayıt yok: ' || p_account_id);
  WHEN OTHERS THEN
    RAISE;
END settle_account;
/
