"""Marketing: what each program appears to do, in two time windows, and what the data cannot show."""
import altair as alt
import pandas as pd
import streamlit as st

from calder_abm import config
from dashboard import common

db, as_of, label, dataset = common.selection()
settings = config.settings()
short, long = int(settings["windows"]["short_days"]), int(settings["windows"]["long_days"])
common.header(
    "Marketing: what the data can and cannot show",
    "Every result on this page describes what was **observed** in counts of accounts. None of it shows that a "
    "program caused a sale: that needs a group of comparable accounts deliberately left out, which this data "
    "does not have.")

campaigns = common.query("SELECT campaign_id, name, channel, planned_budget_usd FROM campaigns ORDER BY campaign_id")
names = dict(zip(campaigns.campaign_id, campaigns.name))
verdicts = common.query("SELECT * FROM budget_recommendations WHERE as_of_date = ?", (as_of,))
VERDICT_ORDER = ["Increase", "Continue", "Reduce", "Stop", "Insufficient evidence"]

spend = common.metric_value("portfolio", "all", "spend_to_date_usd") or 0
unassigned = common.metric_value("portfolio", "all", "unassigned_spend_usd") or 0
programs = verdicts[verdicts.subject_type == "campaign"]
with st.container(horizontal=True):
    st.metric("Spend to date", common.money(spend), f"of {common.money(settings['goal']['marketing_budget_usd'])} budget",
              delta_color="off", delta_arrow="off", border=True, help="KNOWN: invoices in the marketing spend file.")
    for verdict in ["Increase", "Continue", "Reduce"]:
        st.metric(verdict, int((programs.verdict == verdict).sum()), "programs", delta_color="off", delta_arrow="off",
                  border=True)
    st.metric("Too early or too few", int(programs.verdict.isin(["Insufficient evidence"]).sum()), "programs",
              delta_color="off", delta_arrow="off", border=True,
              help="Fewer than 5 target accounts reached, or first reached under 90 days ago.")

# -- verdicts ------------------------------------------------------------------------------
st.subheader("Program verdicts")
level = st.segmented_control("Show", ["Programs", "Channels"], default="Programs", key="mkt_level", required=True,
                             persist_state="session")
subject = "campaign" if level == "Programs" else "channel"
table = verdicts[verdicts.subject_type == subject].copy()
table["order"] = table.verdict.map({v: i for i, v in enumerate(VERDICT_ORDER)})
table = table.sort_values(["order", "spend_to_date_usd"], ascending=[True, False])
table["Program"] = table.subject_id.map(lambda s: names.get(s, s))
channel_of = dict(zip(campaigns.campaign_id, campaigns.channel))
with st.container(border=True):
    for _, r in table.iterrows():
        where = f" ({common.md(channel_of[r.subject_id])})" if r.subject_id in channel_of else ""
        evidence = common.badge(r.evidence_level, common.EVIDENCE_COLORS.get(r.evidence_level, "gray"))
        st.markdown(f"{common.verdict_badge(r.verdict)} **{common.md(r.Program)}**{where} {evidence}  \n"
                    f"{common.md(r.reason)}")
with st.expander("The verdict rules, checked in this order", icon=":material/rule:"):
    for rule in config.budget_rules()["verdicts"]:
        st.markdown(f"{common.verdict_badge(rule['verdict'])} {common.md(rule['when'])}")
    st.caption("Written in config/budget_rules.yaml. Accounts are compared like with like: those that reached Aware "
               "or later. Current clients are checked separately because they buy more often anyway.")

# -- 30 days versus one sales cycle ----------------------------------------------------------
st.subheader(f"{short} days versus one sales cycle ({long} days)")
st.markdown(f"For each program: of the target accounts it reached, how many moved up at least one stage within "
            f"{short} days of first being reached, and within {long} days (about one sales cycle). The same "
            "accounts, two windows.")
window = common.query("SELECT subject_id, window_days, metric, value FROM measurements WHERE as_of_date = ? "
                      "AND subject_type = 'campaign' AND window_days IS NOT NULL", (as_of,))
if window.empty:
    st.info("No measurements for this review point.")
else:
    wide = window.pivot_table(index=["subject_id", "window_days"], columns="metric", values="value").reset_index()
    wide = wide[wide.accounts_reached > 0]
    wide["Program"] = wide.subject_id.map(lambda s: names.get(s, s))
    wide["Window"] = wide.window_days.map(lambda d: f"Within {int(d)} days")
    wide["Share"] = wide.advanced_a_stage / wide.accounts_reached
    wide["Counts"] = wide.apply(lambda r: f"{int(r.advanced_a_stage)} of {int(r.accounts_reached)}", axis=1)
    wide["Not yet complete"] = wide.windows_not_yet_complete.fillna(0).astype(int)
    order = list(wide[wide.window_days == long].sort_values("Share", ascending=False).Program)
    base = alt.Chart(wide).encode(y=alt.Y("Program:N", sort=order, title=None,
                                          axis=alt.Axis(labelLimit=260, labelOverlap=False)))
    lines = base.mark_line(color="#bdbdbd").encode(x="Share:Q", detail="Program:N")
    dots = base.mark_circle(size=140, opacity=1).encode(
        x=alt.X("Share:Q", title="Share of reached accounts that moved up a stage", axis=alt.Axis(format="%")),
        color=alt.Color("Window:N", title=None, scale=alt.Scale(range=["#fd8d3c", "#3182bd"]),
                        legend=alt.Legend(orient="bottom")),
        tooltip=["Program:N", "Window:N", alt.Tooltip("Counts:N", title="Moved up"),
                 alt.Tooltip("Not yet complete:Q", title="Accounts whose window is not over yet")],
    )
    st.altair_chart((lines + dots).properties(height=max(260, 34 * len(order))),
                    alt=f"Share of reached accounts that moved up a stage within {short} and {long} days, by program")
    short_avg = wide[wide.window_days == short].advanced_a_stage.sum()
    long_avg = wide[wide.window_days == long].advanced_a_stage.sum()
    st.markdown(
        f"Judged on a {short}-day window, most programs look like failures; judged over a full sales cycle, most "
        f"look like successes, because most target accounts move eventually whatever they were shown. Added up "
        f"across programs: {int(short_avg)} account moves within {short} days against {int(long_avg)} within "
        f"{long} days. **Neither window says what caused the move**; the comparisons below at least ask whether "
        "reached accounts moved more often than accounts that were not reached.")
    st.caption(f"Accounts reached in the last {long} days have not had a full window yet; hover for how many. "
               "Programs that reached no target account (such as PR) cannot be measured this way at all.")

# -- comparisons ---------------------------------------------------------------------------------
st.subheader("Did reached accounts progress more often?")
comparisons = common.query("SELECT * FROM outcome_comparisons WHERE as_of_date = ?", (as_of,))
choices = []
for kind, title in [("campaign", "Program"), ("channel", "Channel"), ("signal type", "Signal")]:
    keys = sorted(comparisons[comparisons.activity_kind == kind].activity.unique())
    for key in keys:
        text = names.get(key, key) if kind == "campaign" else (
            config.signal_label(key) if kind == "signal type" else key)
        choices.append((kind, key, f"{title}: {text}"))
if not choices:
    st.info("No comparisons at this review point.")
else:
    labels = {f"{k}|{a}": t for k, a, t in choices}
    default = "campaign|CMP-03" if "campaign|CMP-03" in labels else next(iter(labels))
    picked = st.selectbox("Activity", list(labels), index=list(labels).index(default), format_func=labels.get,
                          key="mkt_activity", persist_state="session")
    kind, key = picked.split("|", 1)
    rows = comparisons[(comparisons.activity_kind == kind) & (comparisons.activity == key)]
    for comparison, explain in [
        ("Advanced vs not", "Among accounts that reached Aware or later: did those with the activity move into the "
                            "pipeline (Qualified Lead or later) more often?"),
        ("Signed vs lost", "Among closed accounts that reached Proposal / RFP: did those with the activity sign more "
                           "often?")]:
        part = rows[rows.comparison == comparison]
        if part.empty:
            continue
        overall = part[part.segment == "All accounts"].iloc[0]
        with st.container(border=True):
            st.markdown(f"**{comparison}** {common.badge(overall.evidence_level, common.EVIDENCE_COLORS.get(overall.evidence_level, 'gray'))}")
            st.caption(explain)
            st.markdown(common.md(overall.statement))
            view = part.assign(
                With=part.apply(lambda r: f"{int(r.group_a_with)} of {int(r.group_a_total)}", axis=1),
                Without=part.apply(lambda r: f"{int(r.group_b_with)} of {int(r.group_b_total)}", axis=1),
                Chance=part.could_be_chance.map({1: "could be chance", 0: "unlikely to be chance"}).fillna(""),
            )[["segment", "With", "Without", "evidence_level", "Chance"]]
            st.table(view.rename(columns={"segment": "Accounts", "With": "With the activity",
                                          "Without": "Without it", "evidence_level": "Evidence",
                                          "Chance": "Chance check"}).set_index("Accounts"),
                     border="horizontal", alt=f"{comparison} counts for the selected activity")
    st.caption("Evidence levels: Insufficient evidence (under 5 accounts on a side), No clear difference (under 25 "
               "points apart), Association (seen more often, not proof), Consistent pattern (seen before progress, "
               "for current clients and new relationships alike, and unlikely to be chance). The chance check is "
               "Fisher's exact test.")

# -- credit conventions ------------------------------------------------------------------------------
st.subheader("Who gets credit for the signed deals depends on the rule you pick")
credit = common.query("SELECT subject_id, metric, value, sample_size FROM measurements WHERE as_of_date = ? "
                      "AND subject_type = 'channel' AND metric LIKE 'credit_%_deals'", (as_of,))
if credit.empty:
    st.info("No signed deals yet at this review point, so there is nothing to credit.", icon=":material/info:")
else:
    conventions = config.budget_rules()["credit_conventions"]
    credit["Rule"] = credit.metric.str.removeprefix("credit_").str.removesuffix("_deals").map(
        {c["id"]: c["label"] for c in conventions})
    rule_order = [c["label"] for c in conventions]
    totals = credit.groupby("subject_id").value.sum().sort_values(ascending=False)
    credit["Channel"] = credit.subject_id
    credit["Deals"] = credit.value.round(1)
    heat = alt.Chart(credit).encode(
        x=alt.X("Rule:N", sort=rule_order, title=None, axis=alt.Axis(labelAngle=0, orient="top")),
        y=alt.Y("Channel:N", sort=list(totals.index), title=None, axis=alt.Axis(labelLimit=220)),
    )
    cells = heat.mark_rect(stroke="white").encode(
        color=alt.Color("Deals:Q", title="Deals credited", scale=alt.Scale(scheme="blues")),
        tooltip=["Channel:N", "Rule:N", alt.Tooltip("Deals:Q", format=".1f")])
    text = heat.mark_text().encode(
        text=alt.Text("Deals:Q", format=".1f"),
        color=alt.condition(alt.datum.Deals > credit.Deals.max() * 0.6, alt.value("white"), alt.value("#333")))
    st.altair_chart((cells + text).properties(height=max(220, 34 * len(totals))),
                    alt="Signed deals credited to each channel under five credit rules")
    leaders = common.metric("portfolio", "all", "credit_leader_changes")
    no_touch = common.metric_value("portfolio", "all", "won_deals_without_recorded_touches") or 0
    n_deals = int(credit.sample_size.max() or 0)
    st.markdown(f"Signed deals with a recorded marketing touch: **{n_deals}**. Deals credited to each channel under "
                f"five common rules (fractions where a rule splits a deal). Leading channel by rule: "
                f"{common.md(leaders['note']) if leaders else 'none'}.")
    with st.expander("The five rules", icon=":material/rule:"):
        for c in conventions:
            st.markdown(f"**{c['label']}**: {common.md(c['rule'])}")
    untouched = (f"{int(no_touch)} signed {'deal' if no_touch == 1 else 'deals'} had no recorded touch and get no "
                 "credit under any rule" if no_touch else "Every signed deal had at least one recorded touch")
    st.caption(f"Each rule is a convention someone chose, not a measurement. {untouched}; partner dinners and calls "
               "nobody logged get no credit under any rule.")

# -- limits ---------------------------------------------------------------------------------------------
st.subheader("What this data cannot support")
won = int(common.metric_value("portfolio", "all", "signed_to_date") or 0)
months = len(common.query("SELECT DISTINCT month FROM marketing_spend WHERE month <= ?", (as_of[:7],)))
no_reach = [names[c] for c in programs[programs.accounts_reached == 0].subject_id if c in names]
limits = [
    ("Incrementality: did a program cause extra signatures?",
     "Needs a group of comparable target accounts deliberately kept out of a program, chosen at random. None "
     "was. To start: hold out a random fifth of target accounts from one program for the next two quarters."),
    ("Marketing mix modelling",
     f"Needs years of weekly spend and outcomes across many markets. There are {months} months of spend and "
     f"{won} signed engagements; a model would fit noise."),
    ("Data-driven (algorithmic) attribution",
     f"Needs hundreds of conversions to learn from; there are {won}. The five rules above are conventions, and "
     "they disagree."),
    ("Return on each program",
     "Every result here is association. "
     + (f"{', '.join(no_reach)} reached no identifiable target account, so it cannot be judged at all. "
        if no_reach else "")
     + f"{common.money(unassigned)} of invoices were not assigned to any program."),
    ("Activity nobody recorded",
     "Partner conversations, dinners and calls that were never logged get no credit, and stages that depend on "
     "them may be behind."),
]
for title, why in limits:
    st.markdown(f"{common.kind_badge('UNKNOWN')} **{common.md(title)}** {common.md(why)}")

common.legend()
