"""Then and now: what was knowable at each review point, and whether early judgments held up."""
import altair as alt
import pandas as pd
import streamlit as st

from calder_abm import config
from dashboard import common

db, as_of, label, dataset = common.selection()
points = config.review_points()
labels = dict(points)
first, latest = points[0][0], points[-1][0]
goal = config.settings()["goal"]
common.header(
    "Then and now",
    "The same analysis replayed at four dates, each using only the records that had arrived by then. This shows "
    "what the six-month review could and could not see, and whether its judgments held up. This page always shows "
    "all four review points.")

# -- signed engagements against the goal -----------------------------------------------------------
signed = common.query("SELECT account_id, effective_at FROM stage_history WHERE to_stage = 'Signed Agreement'")
with st.container(border=True):
    st.subheader("Signed engagements against the goal")
    if signed.empty:
        st.caption("No engagement signed yet.")
    else:
        months = pd.date_range(goal["launch_date"], latest, freq="MS")
        dates = pd.to_datetime(signed.effective_at.str[:10])
        curve = pd.DataFrame({"Month": months, "Signed": [int((dates < m + pd.offsets.MonthBegin(1)).sum())
                                                          for m in months]})
        line = alt.Chart(curve).mark_line(point=True, interpolate="step-after").encode(
            x=alt.X("Month:T", title=None, axis=alt.Axis(format="%b %Y")),
            y=alt.Y("Signed:Q", title="Signed engagements (cumulative)"),
            tooltip=[alt.Tooltip("Month:T", format="%b %Y"), "Signed:Q"])
        target = pd.DataFrame({"Month": [pd.Timestamp(goal["goal_date"])], "Signed": [goal["engagements"]],
                               "Label": [f"Goal: {goal['engagements']} by {common.day(goal['goal_date'])}"]})
        goal_dot = alt.Chart(target).mark_point(shape="diamond", size=220, color="#d62728", filled=True).encode(
            x="Month:T", y="Signed:Q", tooltip=["Label:N"])
        goal_text = alt.Chart(target).mark_text(align="right", dx=-12, dy=-12, color="#d62728").encode(
            x="Month:T", y="Signed:Q", text="Label:N")
        reviews = pd.DataFrame({"Month": [pd.Timestamp(d) for d, _ in points], "Review": [l for _, l in points]})
        rules = alt.Chart(reviews).mark_rule(strokeDash=[3, 3], color="#9e9e9e").encode(
            x="Month:T", tooltip=["Review:N", alt.Tooltip("Month:T", format="%b %d, %Y")])
        st.altair_chart((rules + line + goal_dot + goal_text).properties(height=280),
                        alt="Cumulative signed engagements by month against the goal")
        by_goal = int((dates <= pd.Timestamp(goal["goal_date"])).sum())
        st.markdown(f"{common.kind_badge('KNOWN')} {by_goal} engagements signed by the 18-month goal date against a "
                    f"goal of {goal['engagements']}; {len(signed)} by {common.day(latest)}. Dashed lines are the "
                    "four review points.")

# -- what each review point could see ------------------------------------------------------------------
st.subheader("What each review point could see")
portfolio = common.query("SELECT as_of_date, metric, value FROM measurements WHERE subject_type = 'portfolio' "
                         "AND window_days IS NULL")
verdicts = common.query("SELECT as_of_date, subject_id, verdict FROM budget_recommendations "
                        "WHERE subject_type = 'campaign'")
rows = {
    "target_accounts_reached": "Target accounts reached by any program",
    "accounts_engaged": "Accounts engaged (Aware or later)",
    "accounts_qualified_or_later": "Accounts that reached Qualified Lead or later",
    "signed_to_date": "Signed engagements",
    "lost_to_date": "Lost deals",
    "median_people_engaged_per_engaged_account": "Median people engaged per engaged account",
    "median_days_first_touch_to_first_meeting": "Median days from first touch to first meeting",
}
table = portfolio[portfolio.metric.isin(rows)].pivot_table(index="metric", columns="as_of_date", values="value")
table = table.reindex(list(rows)).rename(index=rows)
for d, _ in points:
    v = verdicts[verdicts.as_of_date == d]
    table.loc["Programs with enough evidence to judge", d] = int((v.verdict != "Insufficient evidence").sum())
    table.loc["Programs marked Increase", d] = int((v.verdict == "Increase").sum())
    table.loc["Programs marked Reduce or Stop", d] = int(v.verdict.isin(["Reduce", "Stop"]).sum())
    table.loc["Records waiting for review", d] = int(common.query(
        "SELECT COUNT(*) AS n FROM identity_matches WHERE decision = 'Needs review' "
        "AND (record_date IS NULL OR record_date <= ?)", (f"{d}T23:59:59Z",)).n.iloc[0])
table.columns = [f"{labels[d]} ({common.day(d)})" for d in table.columns]
table.index.name = "Measure"
st.table(table.map(lambda v: "" if pd.isna(v) else f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"),
         border="horizontal", alt="Leading signs and results at each review point")
st.caption("At the six-month review almost nothing has had time to close, so only leading signs (reach, engagement, "
           "people involved, time to a first meeting) can be read. Results arrive later.")

# -- verdicts over time -----------------------------------------------------------------------------------
st.subheader("How the program verdicts changed")
names = dict(common.query("SELECT campaign_id, name FROM campaigns").values)
grid = verdicts.pivot_table(index="subject_id", columns="as_of_date", values="verdict", aggfunc="first")
grid = grid[[d for d, _ in points if d in grid.columns]]
grid.index = [names.get(i, i) for i in grid.index]
grid.columns = [labels[d] for d in grid.columns]
order = ["Increase", "Continue", "Reduce", "Stop", "Insufficient evidence"]
colors = ["#31a354", "#3182bd", "#e6550d", "#d62728", "#9e9e9e"]
st.dataframe(
    grid.map(lambda v: [v] if isinstance(v, str) else []), height="content",
    column_config={c: st.column_config.MultiselectColumn(c, options=order, color=colors) for c in grid.columns},
    alt="Budget verdict for each program at each review point",
)
st.caption("Early verdicts are mostly 'Insufficient evidence': with an 11-month sales cycle, six months is too early "
           "to judge most programs. A verdict can also change when the evidence changes: once a program has reached "
           "almost every account there is no comparison group left, and the verdict falls back to Continue.")

# -- did the six-month priorities hold up? -------------------------------------------------------------------
st.subheader("Did the six-month priorities hold up?")
then = common.query("SELECT s.account_id, a.company_name, s.attention_tier, s.current_stage FROM account_assessments s "
                    "JOIN accounts a USING (account_id) WHERE s.as_of_date = ?", (first,))
now = common.query("SELECT account_id, current_stage AS stage_now FROM account_assessments WHERE as_of_date = ?",
                   (latest,))
merged = then.merge(now, on="account_id")
merged["Outcome"] = merged.stage_now.map(lambda s: s if s in {"Signed Agreement", "Lost"} else "Not decided")
summary = merged.pivot_table(index="attention_tier", columns="Outcome", values="account_id", aggfunc="count",
                             fill_value=0)
summary = summary.reindex([t for t in common.TIER_ORDER if t in summary.index])
for column in ["Signed Agreement", "Lost", "Not decided"]:
    if column not in summary.columns:
        summary[column] = 0
summary = summary[["Signed Agreement", "Lost", "Not decided"]]
summary.insert(0, "Accounts", summary.sum(axis=1))
summary.index.name = f"Tier at the {labels[first].lower()}"
flagged = merged[merged.attention_tier.isin(["Act now", "Push"])]
others = merged[~merged.attention_tier.isin(["Act now", "Push"])]
won = lambda df: int((df.stage_now == "Signed Agreement").sum())
left, right = st.columns([3, 2])
with left:
    st.table(summary.rename(columns={"Signed Agreement": f"Signed by {common.day(latest)}"}), border="horizontal",
             alt="Six-month attention tiers against outcomes at the latest review point")
with right:
    st.markdown(f"{common.kind_badge('KNOWN')} Of **{len(flagged)}** accounts ranked Act now or Push at the "
                f"{labels[first].lower()}, **{won(flagged)}** signed by {common.day(latest)}; of the other "
                f"**{len(others)}**, **{won(others)}** did.")
    rate = lambda df: won(df) / len(df) if len(df) else 0
    quiet_then = merged[(merged.stage_now == "Signed Agreement") & merged.current_stage.isin(["Target", "Aware"])]
    direction = ("pointed the right way: ranked accounts signed more often" if rate(flagged) > rate(others)
                 else "did not separate later signers from the rest")
    st.markdown(f"{common.kind_badge('INFERRED')} The six-month ranking {direction}. "
                f"{len(quiet_then)} of the {won(merged)} later signings came from accounts still at Target or Aware "
                "then, with nothing yet to rank them on. Early rankings guide effort; they do not replace looking "
                "again at each review.")
    st.caption("Outcomes are as recorded at the latest review point in this dataset.")
with st.expander("The accounts behind these numbers", icon=":material/table_chart:"):
    st.dataframe(merged.sort_values(["attention_tier", "company_name"]).rename(columns={
        "company_name": "Account", "attention_tier": f"Tier at {labels[first]}",
        "current_stage": f"Stage at {labels[first]}", "stage_now": f"Stage at {labels[latest]}"})
        [["Account", f"Tier at {labels[first]}", f"Stage at {labels[first]}", f"Stage at {labels[latest]}"]],
        hide_index=True, alt="Each account's six-month tier and later stage")
