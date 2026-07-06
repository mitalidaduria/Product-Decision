"""
UCARP — STTM Validation Engine + Canonical Customer Entity Model
Implements the source-to-target mapping validation used to catch
RADD A-002 (Loyalty source keyed on email instead of customer_id).

Resume claim: "150+ source-to-target mapping rows, transformation
rules, conflict-resolution logic, evidence-based DQ standards per
source connector. Prevented 100,000+ duplicate customer records."

Author: Mitali Daduria
"""

import pandas as pd, numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Callable
import re

# ── 1. CANONICAL ENTITY MODEL ────────────────────────────────
# 12 attributes, 8-column data dictionary (as per resume)
# This IS the Pimcore data model spec encoded as Python

@dataclass
class EntityAttribute:
    """One row of the 8-column data dictionary."""
    attr_name:       str
    data_type:       str          # str, int, email, phone, enum, date
    is_nullable:     bool
    is_master_key:   bool         # part of the golden record key
    survivorship:    str          # which source wins on conflict
    dq_rule:         str          # the validation rule
    pimcore_field:   str          # target field name in Pimcore MDM
    notes:           str

CANONICAL_CUSTOMER = [
    EntityAttribute('customer_id',   'str',   False, True,
        survivorship='CRM (source of truth)',
        dq_rule='NOT NULL, unique, pattern CUST-[0-9]{8}',
        pimcore_field='customerId',
        notes='RADD A-002: DO NOT use email as join key'),
    EntityAttribute('email',         'email', False, False,
        survivorship='CRM > OMS > Marketplace (first non-null)',
        dq_rule='NOT NULL, valid email format, NOT master key',
        pimcore_field='emailAddress',
        notes='NON-UNIQUE: one customer may have multiple emails'),
    EntityAttribute('first_name',    'str',   False, False,
        survivorship='CRM > OMS',
        dq_rule='NOT NULL, 1-50 chars, no special characters',
        pimcore_field='firstName', notes=''),
    EntityAttribute('last_name',     'str',   False, False,
        survivorship='CRM > OMS',
        dq_rule='NOT NULL, 1-50 chars',
        pimcore_field='lastName', notes=''),
    EntityAttribute('phone',         'phone', True,  False,
        survivorship='Loyalty > CRM (loyalty has verified phone)',
        dq_rule='Nullable, E.164 format if present',
        pimcore_field='phoneNumber', notes=''),
    EntityAttribute('date_of_birth', 'date',  True,  False,
        survivorship='CRM (most reliable)',
        dq_rule='Nullable, ISO-8601, age 18-120 years',
        pimcore_field='dateOfBirth', notes=''),
    EntityAttribute('loyalty_tier',  'enum',  True,  False,
        survivorship='Loyalty (only source with tier data)',
        dq_rule='Enum: BRONZE/SILVER/GOLD/PLATINUM/NULL',
        pimcore_field='loyaltyTier', notes=''),
    EntityAttribute('lifetime_value', 'float', True, False,
        survivorship='OMS (calculated from order history)',
        dq_rule='Nullable, >= 0, max 999999.99',
        pimcore_field='lifetimeValue', notes=''),
    EntityAttribute('acquisition_channel', 'enum', True, False,
        survivorship='Marketplace > CRM',
        dq_rule='Enum: ORGANIC/PAID/REFERRAL/AFFILIATE/NULL',
        pimcore_field='acquisitionChannel', notes=''),
    EntityAttribute('country_code',  'enum',  False, False,
        survivorship='CRM',
        dq_rule='ISO-3166-1 alpha-2, NOT NULL',
        pimcore_field='countryCode', notes=''),
    EntityAttribute('consent_marketing', 'bool', False, False,
        survivorship='Most recent update wins (GDPR)',
        dq_rule='NOT NULL, boolean',
        pimcore_field='consentMarketing',
        notes='GDPR: default False if source absent'),
    EntityAttribute('created_at',    'date',  False, False,
        survivorship='CRM (first seen date)',
        dq_rule='NOT NULL, ISO-8601, not in future',
        pimcore_field='createdAt', notes=''),
]

# Print the 8-column data dictionary
dd = pd.DataFrame([{
    'attribute': a.attr_name, 'type': a.data_type,
    'nullable': a.is_nullable, 'master_key': a.is_master_key,
    'survivorship': a.survivorship,
    'dq_rule': a.dq_rule[:40]+'…',
    'pimcore_field': a.pimcore_field,
    'notes': a.notes[:30] if a.notes else ''
} for a in CANONICAL_CUSTOMER])
print("=== UCARP CANONICAL CUSTOMER ENTITY — 12 ATTRIBUTES ===")
print(dd.to_string(index=False))

# ── 2. SIMULATE THE 5 SOURCES + STTM MAPPING ────────────────
SOURCES = ['CRM', 'OMS', 'Returns', 'Loyalty', 'Marketplace']

# Generate synthetic data for each source
def make_source(name, n, key_field):
    np.random.seed(hash(name) % 1000)
    base_ids = [f'CUST-{i:08d}' for i in np.random.choice(5000, n, replace=False)]
    emails   = [f'user{i}@domain.com' for i in np.random.choice(4000, n)]
    return pd.DataFrame({
        key_field:     base_ids,
        'email':       emails,
        'first_name':  [f'FirstName{i}' for i in range(n)],
        'country':    np.random.choice(['IN','US','GB','DE'], n),
        'source':     name
    })

# RADD A-002: Loyalty uses email (non-unique) as join key — THE BUG
crm       = make_source('CRM',       8000, 'customer_id')  # correct
oms       = make_source('OMS',       6000, 'customer_id')  # correct
returns   = make_source('Returns',   2000, 'customer_id')  # correct
loyalty   = make_source('Loyalty',   5000, 'email')        # ← RADD A-002 BUG
marketplace = make_source('Marketplace', 3500, 'customer_id') # correct

# ── 3. RADD A-002 DETECTION ──────────────────────────────────
print("\n=== STTM VALIDATION: KEY FIELD UNIQUENESS CHECK ===")
for src, df_src in [
    ('CRM', crm), ('OMS', oms), ('Returns', returns),
    ('Loyalty', loyalty), ('Marketplace', marketplace)
]:
    key = 'email' if 'email' in df_src.columns and src == 'Loyalty' else 'customer_id'
    if key in df_src.columns:
        total  = len(df_src)
        unique = df_src[key].nunique()
        dupl   = total - unique
        status = '🚨 RADD A-002: NON-UNIQUE KEY' if dupl > 0 else '✓ Key is unique'
        print(f"  {src:15s} key={key:15s}  total={total:5,}  unique={unique:5,}  dupes={dupl:4,}  {status}")