"""Data quality: how far to trust the data, what is waiting for a person, and how the system was tested."""
import altair as alt
import pandas as pd
import streamlit as st

from calder_abm import config
from dashboard import common

db, as_of, label, dataset = common.selection()
end = f"{as_of}T23:59:59Z"
common.header(
    "Data quality and checks",
    "How far to trust the data behind every other page. The system never forces an uncertain match: when a record "
    "could belong to more than one company, or to none on the list, it waits here for a person.")

dated = "(m.record_date IS NULL OR m.record_date <= ?)"
ISSUE_NAMES = {
    "anonymous_visit": "Visit from an unidentified company", "late_arrival": "Arrived late, placed on its real date",
    "personal_email_domain": "Personal email address only", "uncertain_match": "Uncertain match, waiting for review",
    "missing_email": "Missing email address", "unmatched_company": "Company not on the target list",
    "conflicting_value": "Conflicting or unrecognised value", "missing_company": "No company to link to",
    "hedged_statement": "Hedged note kept as a question", "unreadable_timestamp": "Unreadable date",
    "ambiguous_date": "Date that reads two ways", "duplicate_record": "Exact duplicate removed",
    "missing_contact_id": "CRM contact without an ID", "spend_mismatch": "Spend without a campaign or budget",
    "stage_conflict": "CRM stage contradicts the evidence",
}
OTHER_NAMES = [("Opened within a minute", "Automatic email open, not counted"),
               ("has no amount", "Opportunity without a value"),
               ("never agreed", "Referral without the client's consent"),
               ("no reason recorded", "Lost deal without a reason")]


def issue_name(row) -> str:
    if row.issue_type == "other":
        return next((name for text, name in OTHER_NAMES if text in str(row.details)), "Other")
    return ISSUE_NAMES.get(row.issue_type, row.issue_type.replace("_", " ").capitalize())


decisions = common.query(f"SELECT decision, COUNT(*) AS n FROM identity_matches m WHERE {dated} GROUP BY decision",
                         (end,)).set_index("decision").n
signals = int(common.query("SELECT COUNT(*) AS n FROM signals WHERE is_duplicate = 0 AND occurred_at <= ? "
                           "AND COALESCE(received_at, occurred_at) <= ?", (end, end)).n.iloc[0])
kpis = [
    ("Records read", int(decisions.sum()), "rows that had arrived", "Rows from the 15 source files."),
    ("Linked", int(decisions.get("Linked", 0)), "High confidence", "Matched by target ID, web domain or email."),
    ("Linked as probable", int(decisions.get("Linked as probable", 0)), "name only",
     "Exact company name, no domain or ID: used, but it lowers confidence."),
    ("Waiting for review", int(decisions.get("Needs review", 0)), "not used yet",
     "Could belong to more than one company, or only resembles one; a person decides."),
    ("Not linked", int(decisions.get("Not linked", 0)), "no usable company",
     "Personal email, an internet provider instead of a company, or no company at all."),
]
for column, (title, value, note, tip) in zip(st.columns(len(kpis)), kpis):
    with column:
        st.metric(title, f"{value:,}", note, delta_color="off", delta_arrow="off", border=True, help=tip)
st.caption(f"{signals:,} usable signals came out of these records after duplicates were removed.")

# -- review queue ------------------------------------------------------------------------------------
st.subheader("Records waiting for a person")
queue = common.query(f"""
    SELECT m.input_company_name AS company, m.input_domain AS domain, m.input_email AS email,
           m.input_country AS country, m.match_reason AS reason, m.candidate_account_ids AS candidates,
           r.source_file AS file, r.source_row AS row, m.record_date
    FROM identity_matches m JOIN raw_records r USING (raw_record_id)
    WHERE m.decision = 'Needs review' AND {dated} ORDER BY m.input_company_name, m.record_date""", (end,))
if queue.empty:
    st.success("Nothing is waiting for review in this dataset.", icon=":material/task_alt:")
else:
    queue = queue.fillna({"company": "(no name)", "domain": "", "reason": ""})
    grouped = (queue.groupby(["company", "reason", "file"], dropna=False)
               .agg(Records=("row", "count"), First=("record_date", "min"), Last=("record_date", "max"))
               .reset_index().sort_values("Records", ascending=False))
    grouped["First seen"] = grouped.First.map(lambda d: common.day(d) if isinstance(d, str) else "")
    grouped["Last seen"] = grouped.Last.map(lambda d: common.day(d) if isinstance(d, str) else "")
    st.dataframe(grouped.rename(columns={"company": "Company as written", "reason": "Why it waits", "file": "File"})
                 [["Company as written", "Records", "Why it waits", "File", "First seen", "Last seen"]],
                 hide_index=True, column_config={"Why it waits": st.column_config.TextColumn(width="large")},
                 alt="Records waiting for a person to decide which company they belong to")
    st.caption(f"{len(queue)} records in {len(grouped)} groups. Similar records are grouped; one decision settles "
               "the whole group.")
    with st.expander("How a person settles a record", icon=":material/rule:"):
        st.markdown("Add an entry to `config/identity_overrides.yaml`, then run the pipeline again. The decision "
                    "and who made it are kept with every record it settles. Use `account_id: none` when the record "
                    "belongs to no target account.")
        st.code("""overrides:
  - source_file: external_signals.json   # the file shown in the table
    company_name: ABC Supply             # exactly as written in that file
    account_id: none                     # or the target account it belongs to, such as ACC-001
    decided_by: R. Okafor
    decided_on: "2026-10-14\"""", language="yaml")

# -- issues ------------------------------------------------------------------------------------------------
st.subheader("Data quality notes")
issues = common.query(f"""
    SELECT i.issue_type, i.severity, i.source, i.field_name, i.details
    FROM data_quality_issues i LEFT JOIN identity_matches m USING (raw_record_id)
    WHERE {dated}""", (end,))
if issues.empty:
    st.success("No data quality notes.", icon=":material/task_alt:")
else:
    issues["Issue"] = issues.apply(issue_name, axis=1)
    counts = issues.groupby(["Issue", "severity"]).size().reset_index(name="Records")
    order = list(counts.groupby("Issue").Records.sum().sort_values(ascending=False).index)
    chart = alt.Chart(counts).mark_bar().encode(
        y=alt.Y("Issue:N", sort=order, title=None, axis=alt.Axis(labelLimit=240, labelOverlap=False)),
        x=alt.X("Records:Q", title="Records"),
        color=alt.Color("severity:N", title="Severity", scale=alt.Scale(domain=["Warning", "Info"],
                                                                        range=["#e6550d", "#9ecae1"]),
                        legend=alt.Legend(orient="bottom")),
        tooltip=["Issue:N", "severity:N", "Records:Q"],
    ).properties(height=max(260, 30 * len(order)))
    st.altair_chart(chart, alt="Data quality notes by type and severity")
    st.caption("Warnings can change a result (an unreadable date, an ambiguous match); Info notes record a fix or a "
               "limit (a duplicate removed, a personal email address).")
    kinds = ["All"] + order
    pick = st.selectbox("Show notes of type", kinds, key="dq_kind", persist_state="session")
    shown = issues if pick == "All" else issues[issues.Issue == pick]
    st.dataframe(shown.rename(columns={"severity": "Severity", "source": "Source", "field_name": "Field",
                                       "details": "Details"})[["Issue", "Severity", "Source", "Field", "Details"]]
                 .head(500),
                 hide_index=True, column_config={"Details": st.column_config.TextColumn(width="large")},
                 alt="Data quality notes")
    if len(shown) > 500:
        st.caption(f"Showing the first 500 of {len(shown):,}.")

# -- messy versus clean ----------------------------------------------------------------------------------------
st.subheader("Messy data versus the clean twin")
st.markdown("The same two years, generated twice: once as the firm's systems would really hold it, once with no "
            "mess. Comparing them shows what the mess changes, and whether the system said so.")
messy_path, clean_path = config.project_path("messy_db"), config.project_path("clean_db")
sql = "SELECT account_id, current_stage, confidence FROM account_assessments WHERE as_of_date = ?"
messy = common.query(sql, (as_of,), messy_path)
clean = common.query(sql, (as_of,), clean_path)
both = messy.merge(clean, on="account_id", suffixes=("_messy", "_clean"))
differ = both[both.current_stage_messy != both.current_stage_clean]
names = dict(common.query("SELECT account_id, company_name FROM accounts", path=messy_path).values)
left, right = st.columns([2, 3])
with left:
    stats = pd.DataFrame({
        "Messy data": [int((messy.confidence == c).sum()) for c in ["High", "Medium", "Low", "Insufficient evidence"]],
        "Clean twin": [int((clean.confidence == c).sum()) for c in ["High", "Medium", "Low", "Insufficient evidence"]],
    }, index=["High", "Medium", "Low", "Insufficient evidence"])
    stats.index.name = "Confidence"
    st.table(stats, border="horizontal", alt="Accounts by confidence level in each dataset")
    st.caption("The mess moves accounts from High confidence to lower levels instead of hiding it.")
with right:
    if differ.empty:
        st.markdown(f"{common.kind_badge('KNOWN')} Every account has the same stage in both datasets at this review "
                    "point.")
    else:
        flagged = int(differ.confidence_messy.isin(["Low", "Insufficient evidence"]).sum())
        st.markdown(f"{common.kind_badge('KNOWN')} **{len(differ)}** of {len(both)} accounts have a different stage "
                    f"in the messy data; **{flagged}** of them are flagged Low or Insufficient evidence there.")
        st.dataframe(differ.assign(Account=differ.account_id.map(names)).rename(columns={
            "current_stage_messy": "Stage (messy)", "confidence_messy": "Confidence (messy)",
            "current_stage_clean": "Stage (clean twin)"})[["Account", "Stage (messy)", "Confidence (messy)",
                                                           "Stage (clean twin)"]],
            hide_index=True, alt="Accounts whose stage differs between the messy data and the clean twin")

# -- tests against the answer key ----------------------------------------------------------------------------------
st.subheader("Tests against the generator's answer key")
st.markdown("The synthetic data comes with a hidden answer key: which company each record really belongs to, each "
            "account's true stage, and the patterns planted on purpose (two real effects and three traps). These "
            "checks test the **method**, not Calder Hollis's marketing: on real data there is no answer key.")
results = common.query("SELECT * FROM evaluation_results", path=messy_path)
if results.empty:
    st.info("No evaluation results. Run the full pipeline (python -m calder_abm.pipeline).", icon=":material/info:")
else:
    latest = config.latest_review_point()
    pass_rules = {"high_confidence_links_wrong": lambda v: v == 0, "probable_links_wrong": lambda v: v == 0}

    def show(rows: pd.DataFrame) -> None:
        for _, r in rows.iterrows():
            metric = r.metric.split("::")[0]
            if r.test == "Planted pattern" or metric in pass_rules:
                ok = bool(r.value) if r.test == "Planted pattern" else pass_rules[metric](r.value)
                mark = common.badge("Passed", "green") if ok else common.badge("Check", "red")
            else:
                mark = common.badge("Result", "gray")
            st.markdown(f"{mark} {common.md(r.note)}")

    for test, title in [("Answer key", "Matching and stages against the truth"),
                        ("Clean twin", "What the mess changed"),
                        ("Planted pattern", "Planted patterns: real effects found, traps avoided"),
                        ("Backtest", "Backtest")]:
        part = results[results.test == test]
        part = part[(part.as_of_date == as_of) | ~part.metric.isin(["stages_right", "wrong_stages_flagged",
                                                                     "stages_right_on_clean_data",
                                                                     "mess_caused_mistakes_flagged"])]
        part = part[part.metric != "wrong_stages_flagged"]
        if part.empty:
            continue
        with st.container(border=True):
            st.markdown(f"**{title}**")
            show(part)
    mistakes = results[(results.metric == "wrong_stages_flagged") & (results.as_of_date == as_of)]
    if not mistakes.empty and mistakes.iloc[0].note != "No stage mistakes":
        st.caption(f"Stage mistakes at this review point: {mistakes.iloc[0].note}")
