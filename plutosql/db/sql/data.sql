-- Deterministik sentetik veri. Rastgelelik yerine ORA_HASH kullanılır: aynı parametrelerle her
-- kurulumda aynı satırlar, aynı fiziksel sırayla üretilir.
-- Bağlanan değişkenler: :n_txn, :n_accounts, :n_days

TRUNCATE TABLE ref_fx_rate
/
TRUNCATE TABLE stg_txn_seed
/
TRUNCATE TABLE settlement_seed
/
INSERT INTO ref_fx_rate (currency, rate)
SELECT column_value,
       CASE column_value WHEN 'TRY' THEN 1
            ELSE ROUND(0.5 + MOD(ORA_HASH(column_value, 4294967295, 7), 450000) / 10000, 6) END
  FROM TABLE(sys.odcivarchar2list(
         'TRY','USD','EUR','GBP','CHF','JPY','CAD','AUD','SEK','NOK',
         'DKK','PLN','CZK','HUF','RON','BGN','CNY','SAR','AED','KWD',
         'QAR','INR','KRW','BRL','MXN','ZAR','SGD','HKD','NZD','ILS'))
/
INSERT /*+ APPEND */ INTO stg_txn_seed (txn_id, account_id, currency, amount, txn_date, status)
WITH cur AS (
  SELECT currency,
         ROW_NUMBER() OVER (ORDER BY currency) - 1 AS rn,
         COUNT(*) OVER () AS cnt
    FROM ref_fx_rate
), gen AS (
  SELECT LEVEL AS id FROM dual CONNECT BY LEVEL <= :n_txn
)
SELECT g.id,
       1 + MOD(ORA_HASH(g.id, 4294967295, 1), :n_accounts),
       c.currency,
       ROUND(MOD(ORA_HASH(g.id, 4294967295, 3), 1000000) / 100, 2),
       DATE '2024-01-01' + MOD(ORA_HASH(g.id, 4294967295, 4), :n_days),
       'NEW'
  FROM gen g
  JOIN cur c ON c.rn = MOD(ORA_HASH(g.id, 4294967295, 2), c.cnt)
 ORDER BY g.id
/
COMMIT
/
-- Mutabakat tablosunun başlangıcı: (hesap, gün) çiftlerinin yaklaşık yarısı önceden var,
-- böylece upsert hem UPDATE hem INSERT yolunu kullanır.
INSERT /*+ APPEND */ INTO settlement_seed (account_id, settle_date, total_amount)
SELECT account_id, settle_date,
       ROUND(MOD(ORA_HASH(account_id * 100000 + (settle_date - DATE '2024-01-01'), 4294967295, 6), 100000) / 100, 2)
  FROM (SELECT DISTINCT account_id, TRUNC(txn_date) AS settle_date FROM stg_txn_seed)
 WHERE MOD(ORA_HASH(account_id * 100000 + (settle_date - DATE '2024-01-01'), 4294967295, 5), 2) = 0
 ORDER BY account_id, settle_date
/
COMMIT
/
