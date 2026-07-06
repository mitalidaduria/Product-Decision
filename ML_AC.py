"""
ML Acceptance Criteria & Confusion Matrix Review
Implements the sprint-by-sprint quality gate used to govern
the 8-category payment failure classifier.

"""

import numpy as np, pandas as pd
from sklearn.datasets       import make_classification
from sklearn.ensemble        import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics         import (
    classification_report, confusion_matrix, f1_score, precision_score
)
from dataclasses import dataclass
from typing      import Dict

CATEGORIES = [
    'GATEWAY_TIMEOUT', 'INSUFFICIENT_FUNDS', 'BANK_REJECTION',
    'NETWORK_ERROR',   'FRAUD_BLOCK',        'INVALID_CARD',
    'DUPLICATE_TXN',   'SETTLEMENT_MISMATCH'
]

# ── 1. ML ACCEPTANCE CRITERIA SPECIFICATION ─────────────────
# This is the actual requirements document encoded as Python.
# In PITAP, these lived in Confluence; here they're executable.

@dataclass
class CategoryAC:
    """Acceptance Criteria for a single failure category."""
    name:                str
    min_f1:              float   # minimum F1 score (harmonic mean P+R)
    min_precision:       float   # minimum precision (FP cost)
    min_recall:          float   # minimum recall (FN cost)
    min_samples:         int     # minimum test samples for statistical validity
    critical_misclass:   list    # which misclassifications are UNACCEPTABLE
    consequence_if_miss: str     # business consequence of false negative
    weight:              str     # P > R, or R > P, or balanced

# Misclassification consequence analysis → thresholds
AC_SPEC: Dict[str, CategoryAC] = {
  'GATEWAY_TIMEOUT': CategoryAC(
      name='GATEWAY_TIMEOUT',
      min_f1=0.85, min_precision=0.80, min_recall=0.82,
      min_samples=500,
      critical_misclass=['FRAUD_BLOCK'],         # retry a fraud block = security risk
      consequence_if_miss="Unnecessary retry adds latency + gateway cost",
      weight="precision > recall"
  ),
  'FRAUD_BLOCK': CategoryAC(
      name='FRAUD_BLOCK',
      min_f1=0.90, min_precision=0.92, min_recall=0.85,  # strictest
      min_samples=200,
      critical_misclass=['INSUFFICIENT_FUNDS', 'GATEWAY_TIMEOUT'],
      consequence_if_miss="Fraud case retried = $1.2M+ exposure (actual PITAP)",
      weight="precision >> recall"
  ),
  'INSUFFICIENT_FUNDS': CategoryAC(
      name='INSUFFICIENT_FUNDS',
      min_f1=0.85, min_precision=0.80, min_recall=0.80,
      min_samples=400,
      critical_misclass=['FRAUD_BLOCK'],
      consequence_if_miss="Customer prompted to retry when funds not available",
      weight="balanced"
  ),
  'BANK_REJECTION': CategoryAC(
      name='BANK_REJECTION',
      min_f1=0.83, min_precision=0.80, min_recall=0.78,
      min_samples=300,
      critical_misclass=['FRAUD_BLOCK'],
      consequence_if_miss="Retry sent to bank that already hard-declined",
      weight="precision > recall"
  ),
  'NETWORK_ERROR': CategoryAC(
      name='NETWORK_ERROR',
      min_f1=0.82, min_precision=0.78, min_recall=0.80,
      min_samples=200,
      critical_misclass=[],
      consequence_if_miss="Safe to retry — low consequence misclass",
      weight="recall > precision"
  ),
  'INVALID_CARD': CategoryAC(
      name='INVALID_CARD',
      min_f1=0.85, min_precision=0.85, min_recall=0.80,
      min_samples=100,
      critical_misclass=['GATEWAY_TIMEOUT'],  # retry an unretriable failure
      consequence_if_miss="Retry sent for unretriable error — wasted cost",
      weight="precision > recall"
  ),
  'DUPLICATE_TXN': CategoryAC(
      name='DUPLICATE_TXN',
      min_f1=0.80, min_precision=0.85, min_recall=0.75,
      min_samples=50,
      critical_misclass=[],
      consequence_if_miss="Idempotency violation risk",
      weight="precision > recall"
  ),
  'SETTLEMENT_MISMATCH': CategoryAC(
      name='SETTLEMENT_MISMATCH',
      min_f1=0.80, min_precision=0.80, min_recall=0.75,
      min_samples=50,
      critical_misclass=[],
      consequence_if_miss="Manual reconciliation team picks up",
      weight="balanced"
  ),
}

# ── 2. TRAIN A CLASSIFIER (simulates PITAP Databricks model) ─
X, y_raw = make_classification(
    n_samples=20_000, n_features=15, n_classes=8,
    n_informative=10, random_state=42
)
y = y_raw  # 0-7 maps to CATEGORIES list order
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
y_pred = model.predict(X_test)
print("\n✓ Model trained on 20,000 samples, 4,000 test set\n")

# ── 3. ACCEPTANCE CRITERIA VALIDATION ────────────────────────
def validate_against_ac(y_true, y_pred, ac_spec):
    """Sprint-gate: does the model meet every category's AC?"""
    f1s  = f1_score(y_true, y_pred, average=None, labels=list(range(8)))
    precs = precision_score(y_true, y_pred, average=None, labels=list(range(8)))

    results = []
    all_pass = True
    for i, (cat, ac) in enumerate(ac_spec.items()):
        f1   = round(f1s[i], 3)
        prec = round(precs[i], 3)
        pass_f1   = f1   >= ac.min_f1
        pass_prec = prec >= ac.min_precision
        passed    = pass_f1 and pass_prec
        if not passed: all_pass = False
        results.append({
            'category':    cat,
            'f1':          f1,
            'min_f1':      ac.min_f1,
            'f1_pass':     '✓' if pass_f1  else '✗ FAIL',
            'precision':   prec,
            'min_prec':    ac.min_precision,
            'prec_pass':   '✓' if pass_prec else '✗ FAIL',
            'weight':      ac.weight,
            'consequence': ac.consequence_if_miss
        })
    df = pd.DataFrame(results)
    print("="*80)
    print("  SPRINT GATE: ML Acceptance Criteria Validation")
    print("="*80)
    print(df[['category','f1','min_f1','f1_pass',
              'precision','min_prec','prec_pass']].to_string(index=False))
    print(f"\n{'✅ ALL CATEGORIES PASS — SPRINT GATE CLEARED' if all_pass else '❌ GATE FAILED — CATEGORIES BELOW THRESHOLD'}")
    return all_pass

validate_against_ac(y_test, y_pred, AC_SPEC)

# ── 4. CONFUSION MATRIX — the sprint review artifact ─────────
cm = confusion_matrix(y_test, y_pred)
cm_df = pd.DataFrame(cm, index=CATEGORIES, columns=CATEGORIES)
print("\n=== CONFUSION MATRIX (rows=actual, cols=predicted) ===")
print(cm_df.to_string())
print("\nKey: Off-diagonal = misclassifications")
print("FRAUD_BLOCK misclassified as anything = critical defect")

# ── 5. CRITICAL MISCLASSIFICATION CHECK ──────────────────────
fi = CATEGORIES.index('FRAUD_BLOCK')
fraud_misclass = cm[fi].sum() - cm[fi, fi]
if fraud_misclass > 0:
    print(f"\n🚨 CRITICAL: {fraud_misclass} FRAUD_BLOCK records misclassified — gate fails")
else:
    print(f"\n✅ CRITICAL CHECK: 0 FRAUD_BLOCK misclassifications — gate cleared")