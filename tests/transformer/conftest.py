import pytest

from plutosql.transformer.catalog import StaticCatalog

# Referans boru hattının şeması (Docker kurulumundaki DDL ile aynı tutulmalı).
PIPELINE_TABLES = {
    "STG_TXN": {
        "TXN_ID": "NUMBER(12) NOT NULL",
        "ACCOUNT_ID": "NUMBER(10) NOT NULL",
        "CURRENCY": "VARCHAR2(3)",
        "AMOUNT": "NUMBER(18,2)",
        "TXN_DATE": "DATE",
        "STATUS": "VARCHAR2(10)",
    },
    "REF_FX_RATE": {
        "CURRENCY": "VARCHAR2(3) NOT NULL",
        "RATE": "NUMBER(18,6)",
    },
    "TXN_ENRICHED": {
        "TXN_ID": "NUMBER(12) NOT NULL",
        "ACCOUNT_ID": "NUMBER(10) NOT NULL",
        "TXN_DATE": "DATE",
        "AMOUNT_TRY": "NUMBER(18,2)",
    },
    "SETTLEMENT": {
        "ACCOUNT_ID": "NUMBER(10) NOT NULL",
        "SETTLE_DATE": "DATE NOT NULL",
        "TOTAL_AMOUNT": "NUMBER(18,2)",
    },
}


@pytest.fixture(scope="session")
def pipeline_catalog():
    return StaticCatalog(PIPELINE_TABLES)
