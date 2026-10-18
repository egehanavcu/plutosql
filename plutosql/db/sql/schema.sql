-- Referans boru hattı: Staging -> Transformation -> Settlement (+ kur referans tablosu).
-- tests/transformer/conftest.py içindeki PIPELINE_TABLES ile aynı tutulmalıdır.
-- Her deyim tek başına "/" satırıyla biter (plutosql.db.scripts ayırıcısı).

CREATE TABLE ref_fx_rate (
  currency      VARCHAR2(3)   NOT NULL,
  rate          NUMBER(18,6),
  CONSTRAINT ref_fx_rate_pk PRIMARY KEY (currency)
)
/
CREATE TABLE stg_txn (
  txn_id        NUMBER(12)    NOT NULL,
  account_id    NUMBER(10)    NOT NULL,
  currency      VARCHAR2(3),
  amount        NUMBER(18,2),
  txn_date      DATE,
  status        VARCHAR2(10),
  CONSTRAINT stg_txn_pk PRIMARY KEY (txn_id)
)
/
CREATE TABLE txn_enriched (
  txn_id        NUMBER(12)    NOT NULL,
  account_id    NUMBER(10)    NOT NULL,
  txn_date      DATE,
  amount_try    NUMBER(18,2),
  CONSTRAINT txn_enriched_pk PRIMARY KEY (txn_id)
)
/
CREATE TABLE settlement (
  account_id    NUMBER(10)    NOT NULL,
  settle_date   DATE          NOT NULL,
  total_amount  NUMBER(18,2),
  CONSTRAINT settlement_pk PRIMARY KEY (account_id, settle_date)
)
/
-- Her denemeden önce geri yüklenen değişmez başlangıç durumu.
CREATE TABLE stg_txn_seed AS SELECT * FROM stg_txn WHERE 1 = 0
/
CREATE TABLE settlement_seed AS SELECT * FROM settlement WHERE 1 = 0
/
-- Deneme öncesi sıfırlama. TRUNCATE yüksek su seviyesini (HWM) sıfırlar; doğrudan yol (APPEND)
-- yükleme gecikmeli blok temizliği (delayed block cleanout) bırakmaz. Böylece ölçülen blokta
-- consistent gets önceki denemelerden etkilenmez.
CREATE OR REPLACE PROCEDURE pluto_reset AS
BEGIN
  EXECUTE IMMEDIATE 'TRUNCATE TABLE stg_txn';
  EXECUTE IMMEDIATE 'TRUNCATE TABLE txn_enriched';
  EXECUTE IMMEDIATE 'TRUNCATE TABLE settlement';
  INSERT /*+ APPEND */ INTO stg_txn SELECT * FROM stg_txn_seed;
  COMMIT;
  INSERT /*+ APPEND */ INTO settlement SELECT * FROM settlement_seed;
  COMMIT;
END;
/
