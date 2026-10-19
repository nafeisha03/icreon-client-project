-- Calder Hollis marketing attribution and account prioritization
-- Database schema (SQLite 3)
--
-- Conventions
--   * Times are ISO-8601 text in UTC, e.g. '2026-09-22T14:00:00Z'. Dates are 'YYYY-MM-DD'.
--   * Labels such as stage, strength and confidence are words, never hidden scores.
--   * NULL means "unknown". The system never fills a gap with a guess.
--   * Each pipeline run rebuilds one database per dataset (messy and clean)
--     and saves its conclusions for every review point.

PRAGMA foreign_keys = ON;

-- ===================================================================
-- Group 1: Reference lists (loaded from the config files on every run)
-- ===================================================================

-- The stages an account can be in, in order. Stage names live here,
-- not in the code, so they can change without reprogramming.
CREATE TABLE stages (
    stage            TEXT PRIMARY KEY,          -- e.g. 'Proposal / RFP'
    stage_order      INTEGER NOT NULL UNIQUE,   -- 1 = earliest
    kind             TEXT NOT NULL CHECK (kind IN ('Marketing', 'Pipeline', 'Status')),
    firm_weight_pct  INTEGER CHECK (firm_weight_pct BETWEEN 0 AND 100),  -- the firm's convention, never a forecast
    description      TEXT NOT NULL
);

-- The signal model: what each kind of signal means and how strong it is.
CREATE TABLE signal_types (
    signal_type     TEXT PRIMARY KEY,           -- e.g. 'proposal_requested'
    label           TEXT NOT NULL,              -- e.g. 'Proposal requested'
    category        TEXT NOT NULL
                    CHECK (category IN ('Engagement', 'Calder activity', 'Negative',
                                        'Context', 'Deal fact')),
    direction       TEXT NOT NULL CHECK (direction IN ('Positive', 'Negative', 'Neutral')),
    strength        TEXT NOT NULL
                    CHECK (strength IN ('None', 'Very weak', 'Weak', 'Medium',
                                        'Strong', 'Very strong', 'Decisive')),
    supports_stage  TEXT REFERENCES stages (stage),   -- the stage it can qualify, or NULL
    explanation     TEXT NOT NULL               -- plain-English meaning shown on the dashboard
);

-- The dates the analysis is run for, such as the six-month review.
CREATE TABLE review_points (
    as_of_date  TEXT PRIMARY KEY,               -- e.g. '2025-03-31'
    label       TEXT NOT NULL                   -- e.g. 'Six-month review'
);

-- ===================================================================
-- Group 2: Inputs and audit trail
-- ===================================================================

-- One row per pipeline run: the build log.
CREATE TABLE pipeline_runs (
    run_id           INTEGER PRIMARY KEY,
    dataset_variant  TEXT NOT NULL CHECK (dataset_variant IN ('messy', 'clean')),
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'Running'
                     CHECK (status IN ('Running', 'Succeeded', 'Failed')),
    summary          TEXT                       -- row counts and notes, in plain English
);

-- An untouched copy of every incoming row, exactly as received,
-- including free-text notes.
CREATE TABLE raw_records (
    raw_record_id  INTEGER PRIMARY KEY,
    source         TEXT NOT NULL
                   CHECK (source IN ('target_list', 'marketing', 'finance', 'crm', 'website',
                                     'email', 'events', 'meetings', 'notes', 'partners',
                                     'external')),   -- add a name here for a new source
    source_file    TEXT NOT NULL,
    source_row     INTEGER NOT NULL,            -- line number in the source file
    received_at    TEXT,                        -- when the data reached us (late data shows here)
    payload_json   TEXT NOT NULL,               -- the original row, as JSON
    UNIQUE (source_file, source_row)
);

-- Every problem found while cleaning or matching, and what the system did about it.
CREATE TABLE data_quality_issues (
    issue_id       INTEGER PRIMARY KEY,
    raw_record_id  INTEGER REFERENCES raw_records (raw_record_id),
    source         TEXT NOT NULL,
    issue_type     TEXT NOT NULL
                   CHECK (issue_type IN (
                       'missing_email', 'missing_contact_id', 'missing_company',
                       'unreadable_timestamp', 'ambiguous_date', 'duplicate_record',
                       'late_arrival', 'conflicting_value', 'personal_email_domain',
                       'anonymous_visit', 'uncertain_match', 'unmatched_company',
                       'hedged_statement', 'stage_conflict', 'spend_mismatch', 'other')),
    severity       TEXT NOT NULL CHECK (severity IN ('Info', 'Warning', 'Problem')),
    field_name     TEXT,
    details        TEXT NOT NULL,               -- what was wrong and what the system did
    status         TEXT NOT NULL DEFAULT 'Open'
                   CHECK (status IN ('Open', 'Accepted', 'Fixed'))
);

-- ===================================================================
-- Group 3: Who is who
-- ===================================================================

-- The target companies. The marketing target list is the master list;
-- every other source is matched to it.
CREATE TABLE accounts (
    account_id             TEXT PRIMARY KEY,    -- e.g. 'ACC-001'
    company_name           TEXT NOT NULL,
    domain                 TEXT,                -- primary web domain, e.g. 'abccorp.com'
    industry               TEXT,
    company_size           TEXT,                -- employee band, e.g. '1,000-4,999'
    location               TEXT,                -- city and state or region
    country                TEXT,                -- two-letter code, e.g. 'US'
    account_owner          TEXT,                -- Calder Hollis partner responsible
    target_tier            INTEGER CHECK (target_tier IN (1, 2, 3)),
    existing_relationship  TEXT NOT NULL DEFAULT 'Unknown'
                           CHECK (existing_relationship IN ('Current client, other services',
                                                            'Former client', 'None', 'Unknown')),
    -- copied from the latest review point on every run; never edit it by hand
    current_stage          TEXT NOT NULL DEFAULT 'Target' REFERENCES stages (stage)
);

-- Other names and domains a company appears under, such as 'abc' or 'abc-corp.co.uk'.
CREATE TABLE account_aliases (
    alias_id      INTEGER PRIMARY KEY,
    account_id    TEXT NOT NULL REFERENCES accounts (account_id),
    alias_type    TEXT NOT NULL CHECK (alias_type IN ('name', 'domain')),
    alias_value   TEXT NOT NULL,                -- stored cleaned
    learned_from  TEXT,                         -- where the alias came from
    UNIQUE (account_id, alias_type, alias_value)
);

-- People who work at target companies.
CREATE TABLE contacts (
    contact_id        TEXT PRIMARY KEY,         -- assigned by the system, e.g. 'CON-0102'
    account_id        TEXT REFERENCES accounts (account_id),   -- NULL until the company is known
    name              TEXT,
    email             TEXT UNIQUE,              -- may be missing
    job_title         TEXT,
    department        TEXT,
    seniority         TEXT NOT NULL DEFAULT 'Unknown'
                      CHECK (seniority IN ('C-level', 'VP', 'Director', 'Manager', 'Staff', 'Unknown')),
    buying_role       TEXT NOT NULL DEFAULT 'Unknown'
                      CHECK (buying_role IN ('Decision Maker', 'Influencer', 'Technical Evaluator',
                                             'Legal Reviewer', 'Procurement', 'Finance Approver',
                                             'Executive Sponsor', 'Unknown')),
    crm_contact_id    TEXT,                     -- the CRM's own ID, when there is one
    match_confidence  TEXT CHECK (match_confidence IN ('High', 'Medium', 'Low', 'None')),
    first_seen_at     TEXT,
    last_seen_at      TEXT
);

-- How each incoming record was linked (or not) to a company and a person.
-- The CHECK rules at the end make the database itself refuse a link that
-- breaks the matching policy.
CREATE TABLE identity_matches (
    match_id                  INTEGER PRIMARY KEY,
    raw_record_id             INTEGER NOT NULL UNIQUE REFERENCES raw_records (raw_record_id),
    input_company_name        TEXT,             -- as it arrived
    input_domain              TEXT,
    input_email               TEXT,
    input_country             TEXT,
    matched_account_id        TEXT REFERENCES accounts (account_id),   -- NULL when not linked
    match_confidence          TEXT NOT NULL
                              CHECK (match_confidence IN ('High', 'Medium', 'Low', 'None')),
    match_reason              TEXT NOT NULL,    -- e.g. 'email domain abccorp.com matches ABC Corp'
    decision                  TEXT NOT NULL
                              CHECK (decision IN ('Linked', 'Linked as probable',
                                                  'Needs review', 'Not linked')),
    candidate_account_ids     TEXT,             -- JSON list when several companies could fit
    matched_contact_id        TEXT REFERENCES contacts (contact_id),
    contact_match_confidence  TEXT
                              CHECK (contact_match_confidence IN ('High', 'Medium', 'Low', 'None')),
    contact_match_reason      TEXT,
    reviewed_by               TEXT,             -- filled when a person settles a review case
    reviewed_at               TEXT,
    record_date               TEXT,             -- when the record reached us (or happened); NULL for undated lists
    -- Only linked records carry an account; unsure ones never do.
    CHECK ((decision IN ('Linked', 'Linked as probable')) = (matched_account_id IS NOT NULL)),
    -- "Linked" needs High confidence; "Linked as probable" means Medium.
    CHECK (decision <> 'Linked' OR match_confidence = 'High'),
    CHECK (decision <> 'Linked as probable' OR match_confidence = 'Medium')
);

-- ===================================================================
-- Group 4: What happened
-- ===================================================================

-- Marketing campaigns and programs.
CREATE TABLE campaigns (
    campaign_id         TEXT PRIMARY KEY,       -- e.g. 'CMP-03'
    name                TEXT NOT NULL,
    channel             TEXT NOT NULL
                        CHECK (channel IN ('Email', 'Webinar', 'Conference', 'Roundtable or dinner',
                                           'Content', 'LinkedIn', 'Paid media', 'Technology partners',
                                           'Partner network', 'Website', 'PR')),
    start_date          TEXT,
    end_date            TEXT,
    planned_budget_usd  INTEGER,                -- NULL when unknown; actual spend is in marketing_spend
    description         TEXT
);

-- What was actually spent, month by month (from finance).
CREATE TABLE marketing_spend (
    spend_id       INTEGER PRIMARY KEY,
    raw_record_id  INTEGER REFERENCES raw_records (raw_record_id),
    campaign_id    TEXT REFERENCES campaigns (campaign_id),   -- NULL = not assigned to a campaign
    month          TEXT NOT NULL,               -- 'YYYY-MM'
    category       TEXT NOT NULL
                   CHECK (category IN ('Media', 'Events', 'Content', 'Agency',
                                       'Partner programs', 'Tools', 'Other')),
    amount_usd     INTEGER NOT NULL,            -- a negative amount is a credit note
    note           TEXT
);

-- Sales opportunities as recorded in the CRM.
CREATE TABLE opportunities (
    opportunity_id       TEXT PRIMARY KEY,      -- e.g. 'OPP-012'
    account_id           TEXT NOT NULL REFERENCES accounts (account_id),
    name                 TEXT NOT NULL,
    service_line         TEXT,                  -- e.g. 'AI governance assessment'
    estimated_value_usd  INTEGER,               -- NULL means unknown, never a guess
    crm_stage            TEXT,                  -- as the CRM records it; may disagree with the evidence
    crm_lead_source      TEXT,                  -- what the CRM says started it; often filled in later
    status               TEXT NOT NULL CHECK (status IN ('Open', 'Won', 'Lost')),
    loss_reason          TEXT
                         CHECK (loss_reason IN ('Chose another firm', 'Budget cut or freeze',
                                                'Leadership change', 'Priorities changed',
                                                'Procurement stalled', 'No decision', 'Other')),
    created_date         TEXT,
    expected_close_date  TEXT,                  -- NULL means unknown
    closed_date          TEXT,
    CHECK (loss_reason IS NULL OR status = 'Lost')   -- only lost deals have a loss reason
);

-- Every meaningful interaction, one row each, after cleaning and matching.
CREATE TABLE signals (
    signal_id          TEXT PRIMARY KEY,        -- e.g. 'SIG-00412'
    raw_record_id      INTEGER REFERENCES raw_records (raw_record_id),  -- NULL for system-made signals
    account_id         TEXT REFERENCES accounts (account_id),           -- NULL = not linked to a company
    contact_id         TEXT REFERENCES contacts (contact_id),           -- NULL = person unknown
    campaign_id        TEXT REFERENCES campaigns (campaign_id),
    occurred_at        TEXT,                    -- when it happened; NULL if the date was unreadable
    time_precision     TEXT NOT NULL DEFAULT 'exact'
                       CHECK (time_precision IN ('exact', 'day', 'unknown')),
    received_at        TEXT,                    -- when the data reached us
    arrived_late       INTEGER NOT NULL DEFAULT 0 CHECK (arrived_late IN (0, 1)),
    source             TEXT NOT NULL,
    signal_type        TEXT NOT NULL REFERENCES signal_types (signal_type),
    description        TEXT,
    strength           TEXT NOT NULL
                       CHECK (strength IN ('None', 'Very weak', 'Weak', 'Medium',
                                           'Strong', 'Very strong', 'Decisive')),
    sentiment          TEXT NOT NULL DEFAULT 'Unknown'
                       CHECK (sentiment IN ('Positive', 'Neutral', 'Negative', 'Unknown')),
    match_confidence   TEXT CHECK (match_confidence IN ('High', 'Medium', 'Low', 'None')),
    extraction_method  TEXT NOT NULL DEFAULT 'Structured field'
                       CHECK (extraction_method IN ('Structured field', 'Phrase rule', 'AI')),
    evidence_quote     TEXT,                    -- the sentence a signal was read from
    is_duplicate       INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    duplicate_of       TEXT REFERENCES signals (signal_id),
    is_derived         INTEGER NOT NULL DEFAULT 0 CHECK (is_derived IN (0, 1)),  -- 1 = made by the system
    -- A signal joins a company only through a High or Medium match (or is system-made).
    CHECK (account_id IS NULL OR is_derived = 1 OR match_confidence IN ('High', 'Medium')),
    -- A signal read from free text must keep the sentence it came from.
    CHECK (extraction_method = 'Structured field' OR evidence_quote IS NOT NULL)
);

-- ===================================================================
-- Group 5: What the system concluded
-- ===================================================================

-- Every stage change: when it happened, when we learned it, and why.
CREATE TABLE stage_history (
    history_id           INTEGER PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES accounts (account_id),
    from_stage           TEXT REFERENCES stages (stage),
    to_stage             TEXT NOT NULL REFERENCES stages (stage),
    effective_at         TEXT NOT NULL,         -- when the evidence happened
    learned_at           TEXT,                  -- when that evidence reached us
    rule_id              TEXT NOT NULL,         -- the written rule that fired, e.g. 'STG-PROPOSAL'
    evidence_signal_ids  TEXT NOT NULL,         -- JSON list of signal_ids
    confidence           TEXT NOT NULL
                         CHECK (confidence IN ('High', 'Medium', 'Low', 'Insufficient evidence'))
);

-- Each account's assessment at each review point.
CREATE TABLE account_assessments (
    account_id                      TEXT NOT NULL REFERENCES accounts (account_id),
    as_of_date                      TEXT NOT NULL REFERENCES review_points (as_of_date),
    current_stage                   TEXT NOT NULL REFERENCES stages (stage),
    stage_note                      TEXT,       -- e.g. 'legal reviewing'
    last_active_stage               TEXT REFERENCES stages (stage),  -- for Stalled or Lost
    trend                           TEXT NOT NULL
                                    CHECK (trend IN ('Progressing', 'Steady', 'Slowing',
                                                     'Stalled', 'Unclear')),
    trend_reason                    TEXT NOT NULL,
    confidence                      TEXT NOT NULL
                                    CHECK (confidence IN ('High', 'Medium', 'Low',
                                                          'Insufficient evidence')),
    confidence_reasons              TEXT,       -- JSON list of plain-English reasons
    attention_tier                  TEXT NOT NULL
                                    CHECK (attention_tier IN ('Won', 'Find out', 'Act now', 'Push',
                                                              'Reduce spend', 'Nurture')),
    last_important_signal_id        TEXT REFERENCES signals (signal_id),
    days_since_meaningful_activity  INTEGER,    -- NULL if there has never been any
    usable_signal_count             INTEGER NOT NULL DEFAULT 0,
    open_opportunity_value_usd      INTEGER,    -- NULL when unknown
    positive_summary                TEXT,
    negative_summary                TEXT,
    PRIMARY KEY (account_id, as_of_date)
);

-- The next best action for each account at each review point.
CREATE TABLE recommendations (
    recommendation_id  TEXT PRIMARY KEY,        -- e.g. 'REC-2026-09-30-ACC-001'
    account_id         TEXT NOT NULL REFERENCES accounts (account_id),
    as_of_date         TEXT NOT NULL REFERENCES review_points (as_of_date),
    rule_id            TEXT NOT NULL,           -- playbook rule that fired, e.g. 'A5'
    target_contact_id  TEXT REFERENCES contacts (contact_id),  -- NULL when we only know the role
    target_role        TEXT NOT NULL,           -- e.g. 'General Counsel (Legal Reviewer)'
    action             TEXT NOT NULL,
    channel            TEXT NOT NULL
                       CHECK (channel IN ('Email', 'Partner outreach', 'Meeting',
                                          'Event or webinar', 'LinkedIn', 'Internal')),
    reason             TEXT NOT NULL,           -- one sentence with real dates
    confidence         TEXT NOT NULL
                       CHECK (confidence IN ('High', 'Medium', 'Low', 'Insufficient evidence')),
    priority_rank      INTEGER,                 -- order on the dashboard; 1 = first
    created_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'Open'
                       CHECK (status IN ('Open', 'Done', 'Dismissed')),
    UNIQUE (account_id, as_of_date)             -- one next action per account per review point
);

-- What we KNOW, what we INFER and what is UNKNOWN behind each recommendation.
CREATE TABLE recommendation_statements (
    statement_id       INTEGER PRIMARY KEY,
    recommendation_id  TEXT NOT NULL REFERENCES recommendations (recommendation_id),
    kind               TEXT NOT NULL CHECK (kind IN ('KNOWN', 'INFERRED', 'UNKNOWN')),
    statement          TEXT NOT NULL,           -- one plain-English sentence
    evidence_refs      TEXT,                    -- JSON list of signal or record IDs behind a KNOWN fact
    rule_id            TEXT,                    -- the rule behind an INFERRED conclusion
    sort_order         INTEGER NOT NULL DEFAULT 0,
    CHECK (kind <> 'KNOWN' OR evidence_refs IS NOT NULL),   -- a fact must point to its evidence
    CHECK (kind <> 'INFERRED' OR rule_id IS NOT NULL)       -- a conclusion must name its rule
);

-- Figures per campaign, channel, account or the whole program, at each review point:
-- the 30-day and 330-day views, the credit conventions and the six-month leading signs.
CREATE TABLE measurements (
    measurement_id  INTEGER PRIMARY KEY,
    as_of_date      TEXT NOT NULL REFERENCES review_points (as_of_date),
    subject_type    TEXT NOT NULL
                    CHECK (subject_type IN ('campaign', 'channel', 'account', 'portfolio')),
    subject_id      TEXT NOT NULL,              -- campaign_id, channel name, account_id or 'all'
    window_days     INTEGER CHECK (window_days > 0),   -- 30 or 330; NULL when no window applies
    metric          TEXT NOT NULL,              -- e.g. 'accounts_advanced', 'credit_first_touch_usd'
    value           REAL,                       -- NULL when it cannot be measured
    sample_size     INTEGER,                    -- how many accounts the number rests on
    note            TEXT,                       -- e.g. 'Insufficient evidence to compare (3 accounts)'
    UNIQUE (as_of_date, subject_type, subject_id, window_days, metric)
);

-- Won-versus-lost and advanced-versus-not comparisons for each activity.
CREATE TABLE outcome_comparisons (
    comparison_id         INTEGER PRIMARY KEY,
    as_of_date            TEXT NOT NULL REFERENCES review_points (as_of_date),
    activity              TEXT NOT NULL,        -- a campaign_id, channel or signal type
    activity_kind         TEXT NOT NULL CHECK (activity_kind IN ('campaign', 'channel', 'signal type')),
    comparison            TEXT NOT NULL CHECK (comparison IN ('Signed vs lost', 'Advanced vs not')),
    among                 TEXT NOT NULL,        -- who is compared, e.g. 'accounts that reached Proposal / RFP'
    segment               TEXT NOT NULL DEFAULT 'All accounts'
                          CHECK (segment IN ('All accounts', 'Current clients', 'New relationships')),
    group_a_with          INTEGER NOT NULL,     -- e.g. 8 ...
    group_a_total         INTEGER NOT NULL,     -- ... of 14 signed (or advanced) accounts had the activity
    group_b_with          INTEGER NOT NULL,     -- e.g. 3 ...
    group_b_total         INTEGER NOT NULL,     -- ... of 12 lost (or not advanced) accounts did
    came_before_progress  INTEGER CHECK (came_before_progress IN (0, 1)),  -- NULL = mixed or unknown
    could_be_chance       INTEGER CHECK (could_be_chance IN (0, 1)),       -- from Fisher's exact test
    evidence_level        TEXT NOT NULL
                          CHECK (evidence_level IN ('Insufficient evidence', 'No clear difference',
                                                    'Association', 'Consistent pattern',
                                                    'Tested effect')),
    statement             TEXT NOT NULL,        -- the sentence shown, with its counts
    CHECK (group_a_with BETWEEN 0 AND group_a_total),
    CHECK (group_b_with BETWEEN 0 AND group_b_total)
);

-- Continue, Increase, Reduce or Stop for each campaign and channel.
CREATE TABLE budget_recommendations (
    budget_rec_id      INTEGER PRIMARY KEY,
    as_of_date         TEXT NOT NULL REFERENCES review_points (as_of_date),
    subject_type       TEXT NOT NULL CHECK (subject_type IN ('campaign', 'channel')),
    subject_id         TEXT NOT NULL,           -- campaign_id or channel name
    verdict            TEXT NOT NULL
                       CHECK (verdict IN ('Continue', 'Increase', 'Reduce', 'Stop',
                                          'Insufficient evidence')),
    evidence_level     TEXT NOT NULL
                       CHECK (evidence_level IN ('Insufficient evidence', 'No clear difference',
                                                 'Association', 'Consistent pattern',
                                                 'Tested effect')),
    reason             TEXT NOT NULL,
    spend_to_date_usd  INTEGER,                 -- NULL when unknown
    accounts_reached   INTEGER,
    evidence_refs      TEXT,                    -- JSON list of measurement or comparison IDs
    UNIQUE (as_of_date, subject_type, subject_id),
    -- A verdict never rests on too little evidence, and too little evidence is its own verdict.
    CHECK ((verdict = 'Insufficient evidence') = (evidence_level = 'Insufficient evidence'))
);

-- Answers to the six management questions at each review point.
CREATE TABLE executive_summary (
    item_id        INTEGER PRIMARY KEY,
    as_of_date     TEXT NOT NULL REFERENCES review_points (as_of_date),
    question       TEXT NOT NULL
                   CHECK (question IN ('working', 'not_working', 'more_attention',
                                       'less_spend', 'next_budget', 'cannot_determine')),
    statement      TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('KNOWN', 'INFERRED', 'UNKNOWN')),
    confidence     TEXT CHECK (confidence IN ('High', 'Medium', 'Low', 'Insufficient evidence')),
    evidence_refs  TEXT,                        -- JSON list of account, campaign or measurement IDs
    sort_order     INTEGER NOT NULL DEFAULT 0
);

-- How the system did against the answer key, the clean twin, the planted
-- patterns and the backtest.
CREATE TABLE evaluation_results (
    evaluation_id  INTEGER PRIMARY KEY,
    as_of_date     TEXT NOT NULL REFERENCES review_points (as_of_date),
    test           TEXT NOT NULL
                   CHECK (test IN ('Answer key', 'Clean twin', 'Planted pattern', 'Backtest')),
    metric         TEXT NOT NULL,               -- e.g. 'wrong_stages_flagged'
    value          REAL,
    sample_size    INTEGER,
    note           TEXT NOT NULL                -- e.g. '13 of 16 wrong stages were flagged Low or Insufficient evidence'
);

-- ===================================================================
-- Saved view and indexes
-- ===================================================================

-- One chronological timeline per account. Duplicates, unlinked records and
-- records with unreadable dates are left out; they stay visible on the
-- dashboard's data quality page. received_at lets a replay keep only what
-- had arrived by a review point.
CREATE VIEW account_timeline AS
SELECT
    s.account_id,
    s.occurred_at,
    s.received_at,
    s.time_precision,
    s.signal_id,
    s.signal_type,
    t.label        AS signal_label,
    t.direction,
    s.strength,
    s.sentiment,
    s.source,
    s.description,
    s.extraction_method,
    s.evidence_quote,
    s.contact_id,
    c.name         AS contact_name,
    c.buying_role,
    s.campaign_id,
    s.match_confidence,
    s.arrived_late,
    s.is_derived
FROM signals AS s
JOIN signal_types AS t ON t.signal_type = s.signal_type
LEFT JOIN contacts AS c ON c.contact_id = s.contact_id
WHERE s.account_id IS NOT NULL
  AND s.is_duplicate = 0
  AND s.occurred_at IS NOT NULL
ORDER BY s.account_id, s.occurred_at;

CREATE INDEX idx_signals_account_time   ON signals (account_id, occurred_at);
CREATE INDEX idx_signals_type           ON signals (signal_type);
CREATE INDEX idx_contacts_account       ON contacts (account_id);
CREATE INDEX idx_identity_decision      ON identity_matches (decision);
CREATE INDEX idx_stage_history_account  ON stage_history (account_id, effective_at);
CREATE INDEX idx_assessments_date       ON account_assessments (as_of_date);
CREATE INDEX idx_statements_rec         ON recommendation_statements (recommendation_id);
CREATE INDEX idx_spend_campaign_month   ON marketing_spend (campaign_id, month);
CREATE INDEX idx_comparisons_activity   ON outcome_comparisons (as_of_date, activity);
CREATE INDEX idx_issues_type            ON data_quality_issues (issue_type);
