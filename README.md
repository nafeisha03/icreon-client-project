# Calder Hollis AI Assurance: account-based marketing prototype

A working prototype that helps Calder Hollis decide which target accounts need
attention, what to do next with each one, and what the marketing budget appears
to be doing. It runs on two years of **synthetic data**: 120 fictional companies,
their people, emails, meetings, notes, events and CRM records, deliberately made
messy the way real exports are.

Every statement it makes carries a label:

* **KNOWN**: recorded in a source system, with its date and source.
* **INFERRED**: worked out by a written rule, and the rule is named.
* **UNKNOWN**: not in the data; someone has to find out.

When the evidence is too thin or contradictory, it says **Insufficient evidence**
instead of guessing. Marketing results are always *association, not proof*.

---

## Quickest way on a Mac

Double-click **Start dashboard.command** in this folder. A Terminal window opens,
sets everything up the first time (a few minutes; it finds Python 3.12 or newer, or
downloads a private copy with the uv tool, no password needed), then opens the
dashboard in your browser. Leave that window open while you use the dashboard;
close it to stop. If macOS asks whether Terminal may use your Documents folder,
click Allow.

The steps below do the same thing by hand.

## Run it on a Mac by hand (first time, about 10 minutes)

You type the commands below into **Terminal** (press Command+Space, type
`Terminal`, press Return). Copy each line, paste it, press Return, and wait
for it to finish before the next one.

**1. Install Python 3.13** (skip this if `python3 --version` already shows 3.12 or newer).
Download the macOS installer from <https://www.python.org/downloads/>, open it and
click through. Then close Terminal, open it again and check:

```
python3 --version
```

You should see `Python 3.13` followed by more numbers.

**2. Go to the project folder.** If you unzipped it into Documents:

```
cd ~/Documents/calder-hollis-abm
```

**3. Make a private Python environment for the project** (it keeps these add-ons
separate from everything else on your Mac):

```
python3 -m venv .venv
source .venv/bin/activate
```

The start of the Terminal line now shows `(.venv)`.

**4. Install the add-ons** (a minute or two):

```
pip install -r requirements.txt
```

**5. Open the dashboard.** The data and both databases are already built, so this works straight away:

```
streamlit run app.py
```

Terminal shows `Local URL: http://localhost:8501`. Open that address in your
browser (or Command+click it). To stop the dashboard, click in Terminal and press
Control+C.

### Next time

```
cd ~/Documents/calder-hollis-abm
source .venv/bin/activate
streamlit run app.py
```

### Rebuild everything from scratch (optional)

```
python -m calder_abm.generate_data      # writes the synthetic files into data/ (a few seconds)
python -m calder_abm.pipeline           # builds both databases and checks them (about 5 seconds)
```

The pipeline ends by printing its checks against the answer key, for example
`messy data: 0 of 5,674 High-confidence links point to the wrong company` and
`Roundtables (real effect): found (Consistent pattern)`. The same seed always
produces the same data, so the results do not change between runs.

### Check that everything works

```
python -m pytest
```

Expect `123 passed` after about 30 seconds. One of the tests copies the project
to a temporary folder and runs it from nothing, exactly as above.

---

## What is on the dashboard

The sidebar chooses the **data** (the messy data the firm's systems would really
hold, or the clean twin: the same story without the mess) and the **review point**
(six months, 12 months, the 18-month goal date, or 24 months). Each review point
uses only the records that had arrived by that day, so you see what was knowable
then.

| Page | What it answers |
|---|---|
| Pipeline | Where every account stands, what happened after each stage (in counts, not percentages), and which accounts need attention, with the next action for each |
| Account | One company: stage, trend, confidence and why; the next best action (who, what, channel, why); what we know, infer and don't know; the timeline of signals; stage history; people and buying roles; how its records were matched |
| Executive summary | The six management questions, answered in labelled sentences, with a download for the meeting notes |
| Marketing | A verdict for each program (Increase, Continue, Reduce, Stop, or Insufficient evidence), the same accounts measured over 30 and 330 days, the comparisons behind each verdict, five credit rules that disagree, and what this data cannot support |
| Then and now | What each review point could see, how the verdicts changed, and whether the six-month priorities held up |
| Data quality | Records waiting for a person, data quality notes, messy versus clean, and the tests against the generator's answer key |

## Where the rules live

Every threshold and every piece of wording is in a plain-text file in `config/`.
Change a value, run `python -m calder_abm.pipeline` again, and refresh the dashboard.

| File | What it controls |
|---|---|
| `settings.yaml` | Review points, the 30- and 330-day windows, the business goal |
| `stages.yaml` | The ten stages, in James's order; the firm's stage weights are kept here for reference only |
| `signal_model.yaml` | Every kind of signal, its strength and which stage it supports |
| `stage_rules.yaml` | Stalled after 45 quiet days, trend rules, confidence checks, the UNKNOWN questions |
| `action_playbook.yaml` | Attention tiers and the next-best-action rules (A1 to A19) |
| `budget_rules.yaml` | When a comparison counts, the evidence levels, the budget verdicts, the five credit rules |
| `identity.yaml` | How companies and people are matched; buying roles from job titles |
| `identity_overrides.yaml` | Match decisions a person has settled (see the Data quality page) |
| `note_phrases.yaml` | The phrases the notes reader recognises, and the hedge words it never treats as facts |
| `source_mappings.yaml` | Which column in each source file means what: the place to start when real exports arrive |
| `scenario.yaml` | How the synthetic data is generated, including the planted patterns |

## What is in the folder

```
app.py                  the dashboard's front door (streamlit run app.py)
dashboard/              one file per dashboard page
calder_abm/             the pipeline, one file per step:
  generate_data.py        synthetic data, its messy copy and the answer key
  ingest.py, clean.py     read the 15 raw files and log every problem
  identity.py             match records to companies and people
  notes.py, signals.py    turn records and notes into dated signals
  stages.py, uncertainty.py   stages, trend, confidence, KNOWN / INFERRED / UNKNOWN
  recommend.py, prioritize.py next best action and attention tier
  measurement.py, attribution.py   30/330-day views, comparisons, credit rules, budget verdicts
  summary.py              the executive summary
  evaluate.py             checks against the answer key and the clean twin
  pipeline.py             runs every step in order
config/                 the rules (above)
sql/schema.sql          the database layout: 23 tables and one timeline view
data/raw/               the messy synthetic exports (what the system reads)
data/clean/             the same data without the mess
data/answer_key/        the generator's hidden truth, used only by the checks
data/*.db               the two results databases the dashboard reads
tests/                  the automated checks (python -m pytest)
```

## Important limits

* The data is synthetic. The patterns the system finds are the ones the generator
  planted (two real effects and three traps), so the checks test the **method**,
  not Calder Hollis's marketing.
* Nothing here proves that a program caused a sale. That needs a group of
  comparable accounts deliberately left out of a program. The executive summary
  suggests setting one up.
* With 14 signed deals, there is far too little data for marketing mix modelling,
  data-driven attribution or win predictions, and the dashboard says so.
* The notes reader only recognises the phrases in `note_phrases.yaml`. Facts
  written in other words are missed, and the Data quality page counts them.
  An AI reader could be added later, as long as it quotes its evidence.

## If something goes wrong

* **`command not found: python3`**: install Python (step 1), then open a new Terminal window.
* **`externally-managed-environment` when installing**: you skipped step 3. Run the two
  lines in step 3, then step 4 again.
* **`ModuleNotFoundError`**: the private environment is not switched on. Run
  `source .venv/bin/activate` in the project folder.
* **The dashboard says the databases have not been built**: run the two commands under
  "Rebuild everything from scratch".
* **`Port 8501 is already in use`**: the dashboard is already running in another
  Terminal window. Use that one, or run `streamlit run app.py --server.port 8502`
  and open <http://localhost:8502>.
