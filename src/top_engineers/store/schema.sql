-- Three layers: raw (as fetched) -> derived (computed) -> serving (denormalised).
-- The serving layer must let the UI do ZERO computation. That is what makes <10s
-- structural rather than something to tune.

-- ---------------------------------------------------------------- raw
CREATE TABLE IF NOT EXISTS raw_pr (
    number           INTEGER PRIMARY KEY,
    title            VARCHAR,
    url              VARCHAR,
    state            VARCHAR,
    created_at       TIMESTAMP,
    merged_at        TIMESTAMP,
    -- Stored from day one: `updated:>=<last_run>` + skip-unchanged is the 10-50x incremental
    -- win, and retrofitting this column means a full re-fetch.
    updated_at       TIMESTAMP,
    additions        INTEGER,
    deletions        INTEGER,
    changed_files    INTEGER,
    author_login     VARCHAR,
    author_type      VARCHAR,
    merge_commit_oid VARCHAR,
    base_ref_name    VARCHAR,
    body_text        VARCHAR,
    variant          VARCHAR,
    autonomy         VARCHAR,
    hydrated         BOOLEAN DEFAULT FALSE,
    -- CI state of the FIRST pushed commit and of the head commit. Stored at PR level
    -- because statusCheckRollup is far too slow to request per commit.
    ci_first_state   VARCHAR,
    ci_head_state    VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_review (
    pr_number    INTEGER,
    seq          INTEGER,
    author_login VARCHAR,
    author_type  VARCHAR,
    state        VARCHAR,
    submitted_at TIMESTAMP,
    body_text    VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_review_thread (
    pr_number     INTEGER,
    seq           INTEGER,
    is_resolved   BOOLEAN,
    is_outdated   BOOLEAN,
    comment_count INTEGER,
    author_login  VARCHAR,
    body_text     VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_commit (
    pr_number      INTEGER,
    seq            INTEGER,
    oid            VARCHAR,
    committed_date TIMESTAMP,
    conclusion     VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_file (
    pr_number INTEGER,
    path      VARCHAR,
    additions INTEGER,
    deletions INTEGER
);

CREATE TABLE IF NOT EXISTS raw_comment (
    pr_number    INTEGER,
    seq          INTEGER,
    author_login VARCHAR,
    author_type  VARCHAR,
    created_at   TIMESTAMP,
    body_text    VARCHAR
);

-- ---------------------------------------------------------------- cohort
CREATE TABLE IF NOT EXISTS cohort (
    login          VARCHAR PRIMARY KEY,
    authored_n     INTEGER,
    reviewed_n     INTEGER,
    participation  INTEGER,
    rank           INTEGER,
    in_display     BOOLEAN,
    in_normalise   BOOLEAN
);

-- ---------------------------------------------------------------- derived
CREATE TABLE IF NOT EXISTS pr_derived (
    pr_number            INTEGER PRIMARY KEY,
    autonomy             VARCHAR,
    ci_first_pass        BOOLEAN,
    review_rounds        INTEGER,
    risk_weight          DOUBLE,
    has_tests            BOOLEAN,
    touched_files        INTEGER,
    revert_observable    BOOLEAN,
    was_reverted         BOOLEAN,
    time_to_first_review DOUBLE
);

-- One row per person per metric, carrying the whole arithmetic chain so a rank can be
-- explained arithmetically in the drill-down.
CREATE TABLE IF NOT EXISTS person_metric (
    login        VARCHAR,
    metric       VARCHAR,
    pillar       VARCHAR,
    sub_pillar   VARCHAR,
    direction    VARCHAR,
    scored       BOOLEAN,
    n            INTEGER,
    raw          DOUBLE,
    winsorized   DOUBLE,
    shrunk       DOUBLE,
    percentile   DOUBLE,
    weight       DOUBLE,
    contribution DOUBLE,
    std_error    DOUBLE
);

CREATE TABLE IF NOT EXISTS person_score (
    login            VARCHAR PRIMARY KEY,
    score            DOUBLE,
    std_error        DOUBLE,
    ci_low           DOUBLE,
    ci_high          DOUBLE,
    tie_band         INTEGER,
    rank             INTEGER,
    builder_score    DOUBLE,
    reviewer_score   DOUBLE,
    cross_score      DOUBLE,
    authored_n       INTEGER,
    reviewed_n       INTEGER,
    dispatched_n     INTEGER,
    builder_eligible BOOLEAN,
    reviewer_eligible BOOLEAN
);

-- ---------------------------------------------------------------- serving
CREATE TABLE IF NOT EXISTS serving_leaderboard (
    rank              INTEGER,
    login             VARCHAR,
    avatar_url        VARCHAR,
    profile_url       VARCHAR,
    score             DOUBLE,
    ci_low            DOUBLE,
    ci_high           DOUBLE,
    tie_band          INTEGER,
    builder_score     DOUBLE,
    reviewer_score    DOUBLE,
    cross_score       DOUBLE,
    authored_n        INTEGER,
    reviewed_n        INTEGER,
    dispatched_n      INTEGER,
    builder_eligible  BOOLEAN,
    reviewer_eligible BOOLEAN,
    is_display        BOOLEAN
);

CREATE TABLE IF NOT EXISTS serving_metric_chain (
    login         VARCHAR,
    metric        VARCHAR,
    label         VARCHAR,
    pillar        VARCHAR,
    sub_pillar    VARCHAR,
    direction     VARCHAR,
    scored        BOOLEAN,
    n             INTEGER,
    raw           DOUBLE,
    winsorized    DOUBLE,
    shrunk        DOUBLE,
    percentile    DOUBLE,
    weight        DOUBLE,
    contribution  DOUBLE,
    std_error     DOUBLE,
    display_value VARCHAR,
    how_note      VARCHAR
);

CREATE TABLE IF NOT EXISTS serving_evidence (
    login       VARCHAR,
    metric      VARCHAR,
    pr_number   INTEGER,
    url         VARCHAR,
    title       VARCHAR,
    reason      VARCHAR,
    -- Evidence favours PRs explaining a BAD score; those are what people will challenge.
    is_adverse  BOOLEAN,
    sort_key    DOUBLE
);

CREATE TABLE IF NOT EXISTS serving_caveats (
    seq    INTEGER,
    title  VARCHAR,
    body   VARCHAR
);

CREATE TABLE IF NOT EXISTS serving_meta (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);
