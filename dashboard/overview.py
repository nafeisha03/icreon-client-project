"""Pipeline: where every target account stands, and which ones need attention now."""
import altair as alt
import pandas as pd
import streamlit as st

from calder_abm import config
from dashboard import common

db, as_of, label, dataset = common.selection()
common.header("Pipeline and next actions")

goal = config.settings()["goal"]
accounts = common.accounts_at()
signed = int(common.metric_value("portfolio", "all", "signed_to_date") or 0)
by_goal = int(common.metric_value("portfolio", "all", "signed_by_goal_date") or 0)
signed_value = common.metric_value("portfolio", "all", "signed_value_usd") or 0
open_stages = ["Qualified Lead", "Active Conversation", "Proposal / RFP", "Verbal Commitment"]
open_deals = accounts[accounts.current_stage.isin(open_stages)]
open_value = open_deals.open_opportunity_value_usd.fillna(0).sum()
no_value = int(open_deals.open_opportunity_value_usd.isna().sum())
act = int((accounts.attention_tier == "Act now").sum())
push = int((accounts.attention_tier == "Push").sum())
find_out = int((accounts.attention_tier == "Find out").sum())
queue = int(common.query(
    "SELECT COUNT(*) AS n FROM identity_matches WHERE decision = 'Needs review' "
    "AND (record_date IS NULL OR record_date <= ?)", (f"{as_of}T23:59:59Z",)).n.iloc[0])

with st.container(horizontal=True):
    goal_text = (f"{by_goal} by {common.day(goal['goal_date'])} (goal {goal['engagements']})"
                 if as_of >= goal["goal_date"] else f"goal: {goal['engagements']} by {common.day(goal['goal_date'])}")
    st.metric("Signed engagements", signed, goal_text, delta_color="off", delta_arrow="off", border=True,
              help="KNOWN: engagement letters recorded as signed in the CRM.")
    st.metric("Signed value", common.money(signed_value), f"average goal {common.money(goal['average_value_usd'])}",
              delta_color="off", delta_arrow="off", border=True,
              help="KNOWN: estimated values on the signed opportunities in the CRM.")
    st.metric("Open deals", len(open_deals), f"{common.money(open_value)} known value"
              + (f", {no_value} unknown" if no_value else ""), delta_color="off", delta_arrow="off", border=True,
              help="Accounts at Qualified Lead, Active Conversation, Proposal / RFP or Verbal Commitment. "
                   "Stalled accounts are not counted.")
with st.container(horizontal=True):
    st.metric("Act now or push", act + push, f"{act} act now, {push} push", delta_color="off", delta_arrow="off",
              border=True, help="INFERRED from written rules; see the attention tiers below.")
    st.metric("Need finding out", find_out, "evidence contradictory or thin", delta_color="off", delta_arrow="off",
              border=True, help="Accounts whose confidence is Insufficient evidence: confirm the status first.")
    st.metric("Records to review", queue, "possible matches nobody confirmed", delta_color="off",
              delta_arrow="off", border=True,
              help="Records that might belong to a target account but were not linked automatically.")

left, right = st.columns([3, 2])
with left:
    with st.container(border=True):
        st.subheader("Where the accounts stand")
        order = common.stage_names()
        counts = accounts.current_stage.value_counts().reindex(order, fill_value=0).reset_index()
        counts.columns = ["Stage", "Accounts"]
        kind = {s["stage"]: s["kind"] for s in config.stage_list()}
        counts["Kind"] = counts.Stage.map(lambda s: {"Marketing": "Marketing stage", "Pipeline": "Pipeline stage"}
                                          .get(kind[s], "Off track"))
        counts.loc[counts.Stage == "Signed Agreement", "Kind"] = "Signed"
        chart = (
            alt.Chart(counts)
            .mark_bar()
            .encode(
                y=alt.Y("Stage:N", sort=order, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=220)),
                x=alt.X("Accounts:Q", title="Accounts"),
                color=alt.Color("Kind:N", title=None,
                                scale=alt.Scale(domain=["Marketing stage", "Pipeline stage", "Signed", "Off track"],
                                                range=["#9ecae1", "#3182bd", "#31a354", "#bdbdbd"]),
                                legend=alt.Legend(orient="bottom")),
                tooltip=["Stage", "Accounts"],
            )
            .properties(height=360)
        )
        labels_layer = chart.mark_text(align="left", dx=3).encode(text="Accounts:Q", color=alt.value("#444"))
        st.altair_chart(chart + labels_layer, alt=f"Accounts by stage on {common.day(as_of)}")
        st.caption(f"{len(accounts)} target accounts. A stage is the furthest step the evidence supports; "
                   "Stalled means 45 quiet days at Qualified Lead or later.")
with right:
    with st.container(border=True):
        st.subheader("What happened after each stage")
        rows = []
        for stage in config.pipeline_stages():
            reached = int(common.metric_value("portfolio", "all", f"stage_reached::{stage}") or 0)
            won = int(common.metric_value("portfolio", "all", f"stage_signed::{stage}") or 0)
            lost = int(common.metric_value("portfolio", "all", f"stage_lost::{stage}") or 0)
            if stage == "Signed Agreement":
                continue
            rows.append({"Stage reached": stage, "Accounts": reached, "Signed": won, "Lost": lost,
                         "Open": reached - won - lost})
        history = pd.DataFrame(rows).rename(columns={"Open": "Not decided"})
        st.table(history.set_index("Stage reached"), border="horizontal",
                 alt="Accounts that reached each stage and what happened next")
        vc = history.set_index("Stage reached").loc["Verbal Commitment"].rename({"Not decided": "Open"})
        if vc.Accounts:
            st.markdown(f"Of **{vc.Accounts}** accounts that reached Verbal Commitment, **{vc.Signed}** signed and "
                        f"**{vc.Lost}** fell through; {vc.Open} {'is' if vc.Open == 1 else 'are'} still open.")
        st.caption("Counts from this firm's own history so far, not a forecast for any one account. The firm's "
                   "stage weights (20% to 100%) are kept in the design document for reference only.")

st.subheader("Accounts that need attention")
tiers = st.pills("Attention tier", common.TIER_ORDER, selection_mode="multi",
                 default=["Act now", "Find out", "Push"], key="overview_tiers", persist_state="session",
                 format_func=lambda t: f"{t} ({int((accounts.attention_tier == t).sum())})",
                 help="\n\n".join(f"**{t}**: {common.TIER_HELP[t]}" for t in common.TIER_ORDER))
shown = accounts[accounts.attention_tier.isin(tiers or [])].copy()
shown["tier_order"] = shown.attention_tier.map({t: i for i, t in enumerate(common.TIER_ORDER)})
shown = shown.sort_values(["tier_order", "priority_rank"])
shown["Stage"] = shown.current_stage + shown.stage_note.map(lambda n: f" ({n})" if isinstance(n, str) and n else "")
table = shown.rename(columns={
    "company_name": "Account", "attention_tier": "Tier", "trend": "Trend", "confidence": "Confidence",
    "action": "Next action", "target_role": "Who", "channel": "Channel", "reason": "Why",
    "open_opportunity_value_usd": "Known value", "days_since_meaningful_activity": "Quiet days",
})[["Account", "Tier", "Stage", "Trend", "Confidence", "Next action", "Who", "Channel", "Why", "Known value",
    "Quiet days", "account_id"]]
event = st.dataframe(
    table, hide_index=True, on_select="rerun", selection_mode="single-row", key="overview_table",
    column_config={
        "Account": st.column_config.TextColumn(pinned=True),
        "Tier": st.column_config.TextColumn(width="small"),
        "Trend": st.column_config.TextColumn(width="small"),
        "Known value": st.column_config.NumberColumn(format="dollar"),
        "Quiet days": st.column_config.NumberColumn(help="Days since the last meaningful activity"),
        "Why": st.column_config.TextColumn(width="large"),
        "Next action": st.column_config.TextColumn(width="large"),
        "account_id": None,
    },
    alt="Accounts that need attention, with the next best action for each",
)
st.caption("Select a row to open the account. Every next action comes from a written rule in "
           "config/action_playbook.yaml; the reason quotes the dated evidence it used.")
if event and event.selection.rows:
    st.session_state["account_id"] = table.iloc[event.selection.rows[0]]["account_id"]
    st.switch_page("dashboard/account_detail.py")

common.legend()
