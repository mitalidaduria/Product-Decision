"""
pip3-Layer Data Quality Governance Framework
18 automated SQL DQ monitoring queries across 3 gateway sources.

"""

import sqlite3, pandas as pd, numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import random; random.seed(42); np.random.seed(42)

# ── LAYER 1: DATA DICTIONARY APPROVAL GATE ──────────────────
@dataclass
class DataDictionaryEntry:
    field_name:    str;    data_type:  str;     owner:         str
    dq_rule:       str;    sla_threshold: float; severity:     str
    alert_channel: str

DATA_DICTIONARY = {
    'txn_id':       DataDictionaryEntry('txn_id','str','Payments Eng',
        'NOT NULL + UNIQUE',0.0,'CRITICAL','#payments-alerts'),
    'gateway':      DataDictionaryEntry('gateway','enum','Data Eng',
        'NOT NULL, in allowed set',0.0,'CRITICAL','#payments-alerts'),
    'failure_cat':  DataDictionaryEntry('failure_cat','enum','ML Team',
        'NOT NULL post-classification',0.01,'HIGH','#ml-alerts'),
    'amount':       DataDictionaryEntry('amount','float','Payments Eng',
        'NOT NULL, > 0, < 500000',0.001,'HIGH','#payments-alerts'),
    'timestamp':    DataDictionaryEntry('timestamp','datetime','Data Eng',
        'NOT NULL, not in future',0.0,'CRITICAL','#payments-alerts'),
}

# ── GENERATE TEST DATA WITH DELIBERATE DQ ISSUES ────────────
N = 50_000
GATEWAYS = ['RazorpayV2','PayU_Enterprise','HDFC_SmartPay']
CATS     = ['GATEWAY_TIMEOUT','INSUFFICIENT_FUNDS','BANK_REJECTION',
            'NETWORK_ERROR','FRAUD_BLOCK','INVALID_CARD','DUPLICATE_TXN','SETTLEMENT_MISMATCH']

amounts = np.random.exponential(1500, N)
amounts[np.random.choice(N, 50, replace=False)] = -1     # invalid negatives
amounts[np.random.choice(N, 30, replace=False)] = np.nan  # nulls

txn_ids = [f'TXN{i:08d}' for i in range(N)]
txn_ids[100] = txn_ids[50]   # inject duplicate
txn_ids[200] = txn_ids[75]   # inject duplicate

gateways = random.choices(GATEWAYS, k=N)
gateways[1000] = 'UNKNOWN_GW'  # invalid gateway

df = pd.DataFrame({
    'txn_id':     txn_ids,
    'gateway':    gateways,
    'failure_cat': random.choices(CATS, k=N),
    'amount':     amounts,
    'timestamp':  ['2024-01-15 10:00:00'] * N
})

conn = sqlite3.connect(':memory:')
df.to_sql('payment_failures', conn, index=False)

# ── LAYER 2: 18 AUTOMATED DQ MONITORING QUERIES ─────────────
DQ_RULES = [
  # DIMENSION: COMPLETENESS (rules 1–6)
  ("DQ-001","CRITICAL","Completeness","txn_id null rate",0.0,"""
    SELECT ROUND(SUM(CASE WHEN txn_id IS NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-002","CRITICAL","Completeness","gateway null rate",0.0,"""
    SELECT ROUND(SUM(CASE WHEN gateway IS NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-003","HIGH","Completeness","amount null rate",0.1,"""
    SELECT ROUND(SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-004","HIGH","Completeness","failure_cat null rate",1.0,"""
    SELECT ROUND(SUM(CASE WHEN failure_cat IS NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-005","CRITICAL","Completeness","timestamp null rate",0.0,"""
    SELECT ROUND(SUM(CASE WHEN timestamp IS NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-006","MEDIUM","Completeness","overall completeness score",0.5,"""
    SELECT ROUND((1 - (SUM(CASE WHEN amount IS NULL OR gateway IS NULL THEN 1 ELSE 0 END)
    *1.0/(COUNT(*)*2)))*100,2) AS metric FROM payment_failures"""),
  # DIMENSION: UNIQUENESS (rules 7–9)
  ("DQ-007","CRITICAL","Uniqueness","txn_id duplicate rate",0.0,"""
    SELECT ROUND((COUNT(*)-COUNT(DISTINCT txn_id))*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-008","MEDIUM","Uniqueness","distinct gateway count",3.0,"""
    SELECT COUNT(DISTINCT gateway) AS metric FROM payment_failures"""),
  ("DQ-009","LOW","Uniqueness","distinct failure categories",8.0,"""
    SELECT COUNT(DISTINCT failure_cat) AS metric FROM payment_failures"""),
  # DIMENSION: VALIDITY (rules 10–13)
  ("DQ-010","HIGH","Validity","negative amount rate",0.0,"""
    SELECT ROUND(SUM(CASE WHEN amount < 0 THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-011","HIGH","Validity","amount > 500000 rate",0.01,"""
    SELECT ROUND(SUM(CASE WHEN amount > 500000 THEN 1 ELSE 0 END)*100.0/COUNT(*),4)
    AS metric FROM payment_failures"""),
  ("DQ-012","CRITICAL","Validity","invalid gateway values",0.0,"""
    SELECT ROUND(SUM(CASE WHEN gateway NOT IN ('RazorpayV2','PayU_Enterprise','HDFC_SmartPay')
    THEN 1 ELSE 0 END)*100.0/COUNT(*),4) AS metric FROM payment_failures"""),
  ("DQ-013","HIGH","Validity","invalid failure_cat values",0.0,"""
    SELECT ROUND(SUM(CASE WHEN failure_cat NOT IN
    ('GATEWAY_TIMEOUT','INSUFFICIENT_FUNDS','BANK_REJECTION','NETWORK_ERROR',
    'FRAUD_BLOCK','INVALID_CARD','DUPLICATE_TXN','SETTLEMENT_MISMATCH')
    THEN 1 ELSE 0 END)*100.0/COUNT(*),4) AS metric FROM payment_failures"""),
  # DIMENSION: CONSISTENCY (rules 14–16)
  ("DQ-014","MEDIUM","Consistency","gateway failure rate consistency",5.0,"""
    SELECT ROUND(MAX(cnt)*100.0/SUM(cnt) - MIN(cnt)*100.0/SUM(cnt), 2) AS metric
    FROM (SELECT gateway, COUNT(*) AS cnt FROM payment_failures GROUP BY gateway)"""),
  ("DQ-015","LOW","Consistency","max single-category concentration",40.0,"""
    SELECT ROUND(MAX(cnt)*100.0/SUM(cnt),2) AS metric
    FROM (SELECT failure_cat, COUNT(*) cnt FROM payment_failures GROUP BY failure_cat)"""),
  ("DQ-016","MEDIUM","Consistency","avg amount consistency by gateway",500.0,"""
    SELECT ROUND(MAX(avg_amt)-MIN(avg_amt),2) AS metric
    FROM (SELECT gateway, AVG(amount) avg_amt FROM payment_failures
          WHERE amount IS NOT NULL GROUP BY gateway)"""),
  # DIMENSION: TIMELINESS (rules 17–18)
  ("DQ-017","HIGH","Timeliness","record volume (low = missing data)",1000.0,"""
    SELECT COUNT(*) AS metric FROM payment_failures"""),
  ("DQ-018","LOW","Timeliness","distinct days present",1.0,"""
    SELECT COUNT(DISTINCT SUBSTR(timestamp,1,10)) AS metric FROM payment_failures"""),
]

# ── LAYER 3: RUN ALL 18 RULES + GENERATE ALERTS ─────────────
alerts = []; results = []
for rule_id, severity, dimension, label, threshold, sql in DQ_RULES:
    val   = pd.read_sql_query(sql, conn).iloc[0,0]
    breach = False
    # Breach logic depends on whether threshold is max or min
    if dimension == 'Timeliness':
        breach = val < threshold
    elif rule_id == 'DQ-008':
        breach = val != threshold
    else:
        breach = val > threshold
    if breach:
        alerts.append({'rule': rule_id, 'severity': severity, 'label': label,
                        'value': val, 'threshold': threshold})
    results.append({'rule': rule_id, 'dimension': dimension,
                    'label': label[:35], 'value': round(float(val),4),
                    'threshold': threshold, 'severity': severity,
                    'status': '🚨 BREACH' if breach else '✓ OK'})

rdf = pd.DataFrame(results)
print("\n=== 18 DQ MONITORING RULES — FULL REPORT ===")
print(rdf[['rule','dimension','label','value','threshold','status']].to_string(index=False))
print(f"\n📊 SUMMARY: {len(alerts)} breaches out of 18 rules")
if alerts:
    print("\n🚨 ACTIVE ALERTS — would fire to #payments-alerts Slack:")
    for a in alerts:
        print(f"  [{a['severity']}] {a['rule']}: {a['label']} = {a['value']:.4f} (threshold: {a['threshold']})")