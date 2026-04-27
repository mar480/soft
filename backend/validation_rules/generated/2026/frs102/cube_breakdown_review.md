# FRS-102 2026 Cube Breakdown Review

## Summary

- Total topic families: 116
- Total cube occurrences: 241
- Explicitly deprioritised shared technical families:
  - `basic`
  - `empty`

## Confirmed Good Groupings

- `creditors`
  - 9 cube occurrences grouped correctly:
    - base
    - analysis 1
    - segments
    - parents
    - subsidiaries
    - associates
    - joint ventures
    - other parties
    - finance leases

- `debtors`
  - 9 cube occurrences grouped correctly with the same overall pattern as creditors.

- `property_plant_equipment`
  - 4 cube occurrences grouped correctly:
    - base
    - analysis 1
    - range
    - segments

- `operating_leases`
  - 4 cube occurrences grouped correctly:
    - basic
    - main
    - assets
    - liabilities

- `financial_assets`
  - 19 cube occurrences now grouped under the broader financial-assets umbrella.
  - original subfamilies are preserved in cube metadata, including:
    - base financial-assets family
    - basic
    - full
    - simple
    - reclassification
    - associates basic/full
    - subsidiaries basic/full
    - parents basic/full
    - joint ventures basic/full
    - other parties basic/full
    - equities basic/full
    - segments
  - this aligns with the FRC guidance that financial-instrument hypercubes should be treated as manageable variants within a broader disclosure area.

- `financial_liabilities`
  - 7 cube occurrences now grouped under the broader financial-liabilities umbrella.
  - original subfamilies are preserved in cube metadata, including:
    - base financial-liabilities family
    - basic
    - full
    - simple
    - bank-loan grouping
    - segments

- `basic`
  - 10 cube occurrences grouped correctly as a shared technical family:
    - base
    - analysis 1
    - grouping 1-8
  - correctly marked deprioritised.

## Review Candidates

### 1. Acronym / label normalisation still needs refinement

- `equity_and_s_o_c_i`
  - likely should be normalised to something closer to `equity_and_soci`.

- `property_plant_equipment`
  - acceptable for IDs, but the user-facing label should remain `Property, plant and equipment`.

### 2. Financial-instrument hierarchy has been improved, but naming review still remains

The specialised financial-instrument families are no longer treated as peer top-level topics.
They now sit under broader umbrellas, which looks materially closer to the FRC guidance.

Remaining review need:

- confirm whether labels like `Financial Assets Associates Basic` should stay as source-family metadata only
- confirm whether any further umbrella treatment is needed for other technically split families

### 3. Income-family naming is noisy and may need manual cleanup

Examples:

- `income`
- `income_main`
- `income_main_by_ethnicity`
- `income_main_by_ethnicity_main`
- `income_main_by_gender`
- `income_main_by_gender_main`
- `income_main_by_sex`
- `income_main_by_sex_main`
- `income_operating_leases`

These are likely valid separate taxonomy families, but the current naming logic is too literal and should be reviewed before rule generation.

### 4. Some single-cube families with trailing descriptive terms may be semantically fine but should be reviewed

Examples:

- `segments_text`
- `main_industry_sector`
- `streamlined_energy_and_carbon_reporting_basic`

These do not currently look misgrouped, but they should be checked so that we do not promote technical naming artefacts into business-facing rule topics.

## Immediate Recommendations

- Keep the current grouping logic for:
  - creditors
  - debtors
  - PPE
  - operating leases
  - financial assets
  - financial liabilities
  - investments

- Add a next-pass naming normaliser for:
  - taxonomy acronyms
  - SOCI
  - comma-preserving business labels

- Add a review flag for:
  - noisy income-family names
  - generic technical/shared families
