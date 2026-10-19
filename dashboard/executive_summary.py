"""Executive summary: the six management questions, answered with labelled statements."""
import streamlit as st

from dashboard import common

db, as_of, label, dataset = common.selection()
common.header(
    "Executive summary",
    "Six questions for the partners, answered in sentences the system built from its rules. Each one carries a "
    "label: KNOWN facts, INFERRED judgments with their confidence, and what we cannot tell from the data.")

QUESTIONS = [
    ("working", "What appears to be working?", ":material/trending_up:"),
    ("not_working", "What is not working?", ":material/trending_down:"),
    ("more_attention", "Which accounts deserve more attention?", ":material/priority_high:"),
    ("less_spend", "Which accounts should receive less spend?", ":material/savings:"),
    ("next_budget", "Where should the next marketing budget go?", ":material/balance:"),
    ("cannot_determine", "What can we not determine from this data?", ":material/help:"),
]

items = common.query("SELECT question, statement, kind, confidence FROM executive_summary WHERE as_of_date = ? "
                     "ORDER BY sort_order", (as_of,))
if items.empty:
    st.warning("No summary was written for this review point. Run the pipeline again.", icon=":material/warning:")
    st.stop()

lines = [f"# Calder Hollis AI Assurance: executive summary", "",
         f"{label}, {common.day(as_of)} ({dataset}). Synthetic data; all companies are fictional.", ""]
for row_start in range(0, len(QUESTIONS), 2):
    columns = st.columns(2)
    for column, (key, title, icon) in zip(columns, QUESTIONS[row_start: row_start + 2]):
        with column:
            with st.container(border=True, height="stretch"):
                st.markdown(f"#### {icon} {title}")
                answers = items[items.question == key]
                lines += [f"## {title}", ""]
                if answers.empty:
                    st.caption("Nothing to report at this review point.")
                    lines += ["Nothing to report at this review point.", ""]
                for _, r in answers.iterrows():
                    common.statement_line(r.kind, r.statement, r.confidence if isinstance(r.confidence, str) else None)
                    level = f", confidence {r.confidence}" if isinstance(r.confidence, str) and r.kind != "KNOWN" else ""
                    lines.append(f"- [{r.kind}{level}] {r.statement}")
                lines.append("")

lines += ["---", "Marketing results are association, not proof: no comparison group was deliberately left out."]
st.download_button("Download this summary", "\n".join(lines), file_name=f"executive_summary_{as_of}.md",
                   mime="text/markdown", icon=":material/download:",
                   help="A plain-text copy (Markdown) to paste into an email or the meeting notes.")
common.legend()
