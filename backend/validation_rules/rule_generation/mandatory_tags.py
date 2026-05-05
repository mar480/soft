from __future__ import annotations

MANDATORY_TAGS_TOPIC_ID = "mandatory_tags"
MANDATORY_TAGS_TOPIC_LABEL = "Mandatory Tags"


MANDATORY_TAGS = [
    {
        "concept_qname": "bus:UKCompaniesHouseRegisteredNumber",
        "label": "UK Companies House registered number",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:BalanceSheetDate",
        "label": "Balance Sheet Date",
        "required_statement_roles": ["balance_sheet"],
        "notes": "Balance sheet scoped mandatory tag.",
    },
    {
        "concept_qname": "bus:StartDateForPeriodCoveredByReport",
        "label": "Start date for period covered by report",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:EndDateForPeriodCoveredByReport",
        "label": "End date for period covered by report",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:EntityCurrentLegalOrRegisteredName",
        "label": "Entity current legal or registered name",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "core:DateAuthorisationFinancialStatementsForIssue",
        "label": "Date authorisation financial statements for issue",
        "required_statement_roles": ["balance_sheet"],
        "notes": "Balance sheet scoped mandatory tag.",
    },
    {
        "concept_qname": "core:DirectorSigningFinancialStatements",
        "label": "Director signing financial statements",
        "required_statement_roles": ["balance_sheet"],
        "notes": "Balance sheet scoped mandatory tag.",
    },
    {
        "concept_qname": "bus:EntityDormantTruefalse",
        "label": "Entity dormant [true/false]",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:EntityTradingStatus",
        "label": "Entity trading status",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:AccountingStandardsApplied",
        "label": "Accounting standards applied",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:AccountsStatusAuditedOrUnaudited",
        "label": "Accounts status audited or unaudited",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "bus:AccountsType",
        "label": "Accounts type",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "core:AverageNumberEmployeesDuringPeriod",
        "label": "Average number of employees during the period",
        "required_statement_roles": [],
        "notes": "",
    },
    {
        "concept_qname": "core:ProfitLoss",
        "label": "Profit/Loss",
        "required_statement_roles": [],
        "notes": "",
    },
]
