"""Account: one company's stage, evidence, people and next best action."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import common

db, as_of, label, dataset = common.selection()
end = f"{as_of}T23:59:59Z"
accounts = common.accounts_at()
if accounts.empty:
    st.warning("No assessments for this review point.")
    st.stop()

tier_rank = {t: i for i, t in enumerate(common.TIER_ORDER)}
ordered = accounts.assign(_t=accounts.attention_tier.map(tier_rank)).sort_values(["_t", "priority_rank"])
options = list(ordered.account_id)
info = accounts.set_index("account_id")
if st.session_state.get("account_id") not in options:
    st.session_state["account_id"] = options[0]
account_id = st.selectbox(
    "Account", options, key="account_id", persist_state="session",
    format_func=lambda a: f"{info.at[a, 'company_name']} · {info.at[a, 'attention_tier']} · {info.at[a, 'current_stage']}",
    help="Sorted by attention tier, then priority. Type to search.")
a = info.loc[account_id]
account = common.query("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).iloc[0]

relationship = {"None": "No existing relationship", "Unknown": "Relationship not recorded",
                "Former client": "Former client"}.get(account.existing_relationship, account.existing_relationship)
st.title(a.company_name)
st.caption(f"{label}, {common.day(as_of)} · {dataset} · {account.industry or 'Industry unknown'} · "
           f"{account.location or account.country or ''} · Target tier {account.target_tier} · "
           f"{relationship} · Owner: {account.account_owner or 'unknown'}")
value = a.open_opportunity_value_usd
st.markdown(" ".join([common.tier_badge(a.attention_tier), common.confidence_badge(a.confidence),
                      common.badge("No open opportunity in the CRM" if pd.isna(value)
                                   else f"Open opportunity: {common.money(value)} (CRM estimate)", "gray")]),
            help=common.TIER_HELP.get(a.attention_tier))

with st.container(horizontal=True):
    note = a.stage_note if isinstance(a.stage_note, str) and a.stage_note else None
    st.metric("Stage", a.current_stage, note, delta_color="off", delta_arrow="off", border=True,
              help="INFERRED: the furthest stage the evidence supports, by the rules in config/stage_rules.yaml.")
    st.metric("Trend", a.trend, a.trend_reason if isinstance(a.trend_reason, str) else None, delta_color="off",
              delta_arrow="off", border=True, help="INFERRED from the trend rules.")
    quiet = a.days_since_meaningful_activity
    st.metric("Quiet days", "none yet" if pd.isna(quiet) else int(quiet), "since the last meaningful activity",
              delta_color="off", delta_arrow="off", border=True)

# -- next best action ------------------------------------------------------------------
statements = common.query("SELECT kind, statement, evidence_refs, rule_id FROM recommendation_statements "
                          "WHERE recommendation_id = ? ORDER BY sort_order", (a.recommendation_id,))
with st.container(border=True):
    st.subheader("Next best action")
    if isinstance(a.action, str):
        st.markdown(f"#### {common.md(a.action)}")
        st.markdown(f"**Who:** {common.md(a.target_role or 'Nobody suitable on record')}  \n"
                    f"**Channel:** {common.md(a.channel)}  \n"
                    f"**Why:** {common.md(a.reason)}")
        st.markdown(f"{common.confidence_badge(a.action_confidence)} {common.badge(f'Rule {a.rule_id}', 'gray')}",
                    help="The rule is in config/action_playbook.yaml. Confidence comes from the checks listed under "
                         "'What we infer'.")
    known, inferred, unknown = st.columns(3)
    for column, kind, title in [(known, "KNOWN", "What we know"), (inferred, "INFERRED", "What we infer"),
                                (unknown, "UNKNOWN", "What we don't know")]:
        with column:
            st.markdown(f"**{title}** {common.kind_badge(kind)}", help=common.KIND_HELP[kind])
            items = statements[statements.kind == kind]
            if items.empty:
                st.caption("Nothing recorded.")
            for _, s in items.iterrows():
                refs = json.loads(s.evidence_refs) if isinstance(s.evidence_refs, str) else []
                suffix = f" ({', '.join(refs)})" if refs else (f" (rule {s.rule_id})" if pd.notna(s.rule_id) else "")
                st.markdown(f"- {common.md(s.statement)}{common.md(suffix)}")

# -- timeline ----------------------------------------------------------------------------
timeline = common.query("SELECT * FROM account_timeline WHERE account_id = ? AND occurred_at <= ? AND received_at <= ?",
                        (account_id, end, end))
history = common.query("SELECT from_stage, to_stage, effective_at, learned_at, rule_id, evidence_signal_ids, confidence "
                       "FROM stage_history WHERE account_id = ? AND effective_at <= ? AND learned_at <= ? "
                       "ORDER BY effective_at", (account_id, end, end))

st.subheader("Timeline")
with st.container(horizontal=True, vertical_alignment="center"):
    show_ours = st.toggle("Show our own email sends and ad impressions", value=False, key="timeline_ours")
    st.caption(f"{len(timeline)} signals recorded by {common.day(as_of)}, duplicates removed.")
view = timeline if show_ours else timeline[~timeline.signal_type.isin(["outreach_sent", "ad_shown"])]
if view.empty:
    st.info("No signals recorded for this account by this review point.", icon=":material/info:")
else:
    view = view.copy()
    view["Date"] = pd.to_datetime(view.occurred_at, utc=True)
    view["Person"] = view.contact_name.fillna("Someone at the company") + view.buying_role.map(
        lambda r: f" ({r})" if isinstance(r, str) and r != "Unknown" else "")
    view["Detail"] = view.evidence_quote.where(view.evidence_quote.notna(), view.description).fillna("")
    order = list(view.sort_values("Date").drop_duplicates("signal_label").signal_label)
    size = {"Weak": 40, "Medium": 90, "Strong": 160, "Very strong": 240}
    view["Size"] = view.strength.map(size).fillna(40)
    points = (
        alt.Chart(view)
        .mark_point(filled=True, opacity=0.85)
        .encode(
            x=alt.X("Date:T", title=None, axis=alt.Axis(format="%b %Y", tickCount="month", labelOverlap=True)),
            y=alt.Y("signal_label:N", sort=order, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=220)),
            color=alt.Color("direction:N", title="Direction",
                            scale=alt.Scale(domain=["Positive", "Neutral", "Negative"],
                                            range=["#31a354", "#9e9e9e", "#d62728"]),
                            legend=alt.Legend(orient="bottom")),
            size=alt.Size("Size:Q", legend=None),
            tooltip=[alt.Tooltip("Date:T", format="%b %d, %Y"), alt.Tooltip("signal_label:N", title="Signal"),
                     "Person:N", alt.Tooltip("source:N", title="Source"), "Detail:N",
                     alt.Tooltip("match_confidence:N", title="Match")],
        )
    )
    layers = [points]
    if not history.empty:
        moves = history.assign(Date=pd.to_datetime(history.effective_at, utc=True))
        layers.append(alt.Chart(moves).mark_rule(strokeDash=[4, 3], color="#3182bd").encode(
            x="Date:T", tooltip=[alt.Tooltip("Date:T", format="%b %d, %Y"), alt.Tooltip("to_stage:N", title="Stage")]))
    chart = alt.layer(*layers).properties(height=max(240, 30 * len(order)))
    st.altair_chart(chart, alt=f"Signals over time for {a.company_name}; dashed lines mark stage changes")
    st.caption("Bigger dots are stronger signals. Dashed blue lines mark stage changes. Hover for details.")

    with st.expander("All signals as a table", icon=":material/table_chart:"):
        table = view.sort_values("Date", ascending=False)
        table = table.assign(When=table.Date.dt.strftime("%b %d, %Y"),
                             Late=table.arrived_late.map({1: "late", 0: ""}),
                             How=table.extraction_method.map({"Phrase rule": "read from a note",
                                                              "Structured field": "structured record"}))
        st.dataframe(table[["When", "signal_label", "Person", "source", "Detail", "How", "match_confidence", "Late",
                            "signal_id"]].rename(columns={"signal_label": "Signal", "source": "Source",
                                                          "match_confidence": "Match", "signal_id": "ID"}),
                     hide_index=True, alt=f"Every signal for {a.company_name}")

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.subheader("Stage history")
        if history.empty:
            st.caption("No stage change yet: the account is still at Target.")
        else:
            moves = history.assign(When=history.effective_at.map(common.day),
                                   Evidence=history.evidence_signal_ids.map(
                                       lambda r: ", ".join(json.loads(r)) if isinstance(r, str) else ""))
            st.dataframe(moves[["When", "from_stage", "to_stage", "rule_id", "Evidence"]].rename(
                columns={"from_stage": "From", "to_stage": "To", "rule_id": "Rule"}),
                hide_index=True, alt="Stage changes with the rule and evidence behind each")
            st.caption("Each move names the rule in config/stage_rules.yaml and the signal IDs it used.")
with right:
    with st.container(border=True):
        st.subheader("People")
        people = common.query("SELECT contact_id, name, job_title, buying_role, seniority, match_confidence "
                              "FROM contacts WHERE account_id = ?", (account_id,))
        engaged = timeline[timeline.contact_id.notna() & (timeline.direction == "Positive")]
        last = engaged.groupby("contact_id").occurred_at.max()
        count = engaged.groupby("contact_id").size()
        people["Signals"] = people.contact_id.map(count).fillna(0).astype(int)
        people["Last engaged"] = people.contact_id.map(last).map(lambda v: common.day(v) if isinstance(v, str) else "")
        people = people.sort_values("Signals", ascending=False)
        st.dataframe(people.rename(columns={"name": "Name", "job_title": "Title", "buying_role": "Buying role",
                                            "match_confidence": "Match"})
                     [["Name", "Title", "Buying role", "Signals", "Last engaged", "Match"]],
                     hide_index=True, alt=f"People at {a.company_name} and their buying roles")
        roles = set(people[people.Signals > 0]["buying_role"])
        missing = [r for r in ["Decision Maker", "Executive Sponsor", "Influencer"] if r not in roles]
        if missing:
            st.markdown(f"{common.kind_badge('UNKNOWN')} No engagement recorded yet from: {', '.join(missing)}.")
        st.caption("Buying roles are inferred from job titles by the keyword list in config/identity.yaml.")

with st.expander("How records were matched to this account", icon=":material/link:"):
    aliases = common.query("SELECT alias_type, alias_value, learned_from FROM account_aliases WHERE account_id = ?",
                           (account_id,))
    st.markdown("**Names and web domains recognised for this account**")
    st.dataframe(aliases.rename(columns={"alias_type": "Type", "alias_value": "Value", "learned_from": "Learned from"}),
                 hide_index=True, alt="Names and domains linked to this account")
    matches = common.query("""
        SELECT m.decision, m.match_confidence, m.match_reason, m.input_company_name, m.input_domain, r.source_file
        FROM identity_matches m JOIN raw_records r USING (raw_record_id)
        WHERE (m.matched_account_id = ? OR m.candidate_account_ids LIKE ?) AND m.decision != 'Linked'
          AND r.received_at <= ?""", (account_id, f"%{account_id}%", end))
    if matches.empty:
        st.caption("Every record for this account was linked with High confidence.")
    else:
        st.markdown("**Records linked as probable, or waiting for review**")
        st.dataframe(matches.rename(columns={"decision": "Decision", "match_confidence": "Confidence",
                                             "match_reason": "Reason", "input_company_name": "Company as written",
                                             "input_domain": "Domain", "source_file": "File"}),
                     hide_index=True, alt="Uncertain matches involving this account")
        st.caption("Probable matches are used but lower the confidence; records in review are not used until a "
                   "person confirms them in config/identity_overrides.yaml.")

common.legend()
