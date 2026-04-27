# FRS-102 2026 Cube Breakdown Review

## Summary

- Total topic families: 108
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

- `cash_flow`
  - 9 cube occurrences now grouped under the broader cash-flow umbrella.
  - original source families are preserved in cube metadata, including:
    - base cash-flow family
    - other related parties
    - share classes
  - this looks materially closer to the same structural pattern already used for creditors and debtors.

- `equity_and_s_o_c_i`
  - 5 cube occurrences now grouped under the broader equity-and-SOCI umbrella.
  - original source families are preserved in cube metadata, including:
    - base equity-and-SOCI family
    - PPE
    - shares
  - this aligns with the screenshot evidence showing PPE, shares, and segments as specialised SOCI/equity occurrences within one broader family.

- `streamlined_energy_and_carbon_reporting`
  - 6 cube occurrences now grouped under the broader SECR umbrella.
  - original source families are preserved in cube metadata, including:
    - basic
    - basic grouping
    - reporting region
    - reporting region grouping
    - dual reporting type
    - dual reporting type - emissions source
  - this aligns with the screenshot evidence showing one broader SECR family with multiple structural variants.

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

## Structural Review Pass

### Strong candidates for umbrella-family treatment

These families show the same broad pattern as the financial-instrument restructuring: one broad disclosure family plus a set of structurally related specialised occurrences.

#### `cash_flow`

Current state:

- now grouped as one umbrella family containing:
  - analysis
  - base
  - parents
  - subsidiaries
  - associates
  - joint ventures
  - finance leases
  - other related parties
  - share classes

Assessment:

- This restructuring now looks correct.
- No further structural change needed at this stage.

#### `equity_and_s_o_c_i`

Current state:

- now grouped as one umbrella family containing:
  - base
  - analysis
  - segments
  - PPE
  - shares

Assessment:

- This restructuring now looks correct.
- No further structural change needed at this stage.

#### `streamlined_energy_and_carbon_reporting`

Current state:

- now grouped as one umbrella family containing:
  - basic
  - basic grouping 1
  - reporting region
  - reporting region grouping 1
  - dual reporting type
  - dual reporting type - emissions source

Assessment:

- This restructuring now looks correct.
- No further structural change needed at this stage.

### Families that look acceptable as-is for now

#### `income`

Current umbrella family:

- `income`
  - assets
  - associates
  - basic
  - finance leases
  - joint ventures
  - main
  - parents
  - subsidiaries

Related but distinct families:

- `income_main`
- `income_main_by_sex`
- `income_main_by_sex_main`
- `income_main_by_gender`
- `income_main_by_gender_main`
- `income_main_by_ethnicity`
- `income_main_by_ethnicity_main`
- `income_operating_leases`
- `income_p_p_e`
- `income_intangibles`
- `income_biological_assets`
- `income_business_combinations`
- `income_investment_property`
- `income_provisions`
- `income_other_related_parties`
- `income_s_t_and_l_v_operating_leases`

Assessment:

- The `income` area is structurally noisy, but the ELR definitions suggest several genuinely distinct disclosure constructions rather than one simple umbrella.
- The screenshot evidence helps here:
  - `Income - Main - By Sex` plus `By Sex Main Analysis 1`
  - `Income - Main - By Gender` plus `By Gender Main Analysis 1`
  - `Income - Main - By Ethnicity` plus `By Ethnicity Main Analysis 1`
  suggests that each `by-*` family is a paired mini-family with its own analysis occurrence.
- Recommendation: do not aggressively merge `income_*` families yet.
- Instead, improve naming first and review the dimension sets before any hierarchy change.

#### `continuing_discontinued`

Current state:

- base
- analysis
- segments

Assessment:

- Looks internally coherent already.
- No structural change needed at this stage.

#### `income_tax`

Current state:

- base
- analysis
- segments

Assessment:

- Looks internally coherent already.
- No structural change needed at this stage.

### Recommended next structural pass

If we continue structural cleanup before Phase 3, the highest-value candidates are:

1. Review whether any remaining non-financial families need hierarchy comparable to the now-completed:
   - financial assets
   - financial liabilities
   - cash flow
   - equity and SOCI
   - streamlined energy and carbon reporting

The `income_*` area should be reviewed more cautiously, because it looks more like a mixture of genuinely different topic families plus some naming noise rather than a single obvious umbrella family.
