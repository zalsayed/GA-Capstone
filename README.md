# GA-Capstone

This repository contains 2 tool developed for QA  

1. ContentAuditor that audits Bahrain.bh service pages

A tool that automatically checks Bahrain government service pages for quality issues in both Arabic and English — and tells you exactly what's wrong and how to fix it.

What it does
You give it a government service page. It gives you a list of issues — spelling errors, missing links, unclear descriptions, untranslated content, vague attachment names, contradictions between languages, and more. Each issue comes with the exact location on the page and a ready-to-use fix.
It audits one service in under 60 seconds. A manual audit takes 30–40 minutes.

How it works
The tool uses two detection methods that work together:
Rules — a checklist of 30+ specific things that are always objectively wrong. Arabic spelling errors, missing hyperlinks on regulations, empty sections, fee mismatches between Arabic and English. These run instantly with no API cost.
AI — Google Gemini reads the page and catches issues that require judgment. Unclear descriptions, translation mismatches, vague eligibility criteria, contradictory submission channels. Things a rule cannot detect.
Both run on every page. If they find the same issue, it only appears once in the report.

Requirements
pip install requests beautifulsoup4 playwright streamlit
playwright install chromium
You need a Gemini API key (free at aistudio.google.com) for AI mode. Rules-only mode needs no key.

Quick start
Audit one service by ID:
python3 auditor_v2.py \
  --psid 3182 \
  --key YOUR_GEMINI_KEY \
  --entity "Ministry of Social Development"
Audit from a saved HTML page:
python3 auditor_v2.py \
  --html page.html \
  --key YOUR_GEMINI_KEY \
  --entity "NHRA"
Audit multiple services from a CSV:
python3 auditor_v2.py \
  --psid-file reports/services.csv \
  --key YOUR_GEMINI_KEY \
  --entity "Civil Service Bureau"
Use the web interface instead:
streamlit run app.py

Audit modes
Mode	What runs	API key needed	Speed
--mode rules	Rule engine only	No	Instant
--mode ai	AI only	Yes	~35s/service
--mode full	Both (default)	Yes	~40s/service
Use --mode rules when you hit rate limits or want a quick structural check.

Output
Each run produces a CSV file in reports/ with these columns:
Column	What it contains
Entity	The government entity
Page	Service name linked to the page
Issue	Placement + Description + Solution
Status	New Issue
Additional Comments	Screenshot link if enabled
The Issue cell contains three parts — where the problem is, what the problem is, and the exact corrected text ready to copy and paste.

Options
--psid 1634 1635          # Audit specific service IDs
--esid 230 456            # Audit eService IDs
--html page.html          # Audit from saved HTML file
--psid-file services.csv  # Audit from a CSV of service IDs
--key AIza...             # Gemini API key
--groq-key gsk_...        # Groq key (fallback if Gemini hits limits)
--openrouter-key sk-or-.. # OpenRouter key (second fallback)
--mode full               # full / rules / ai
--workers 3               # How many services to audit in parallel
--screenshots             # Take screenshots of each issue location
--drive-key client.json   # Upload screenshots to Google Drive
--drive-folder FOLDER_ID  # Google Drive folder ID
--entity "Name"           # Entity name for the report
--reviewer                # Review issues interactively after audit
--resume                  # Resume a run that was interrupted

If a run fails or gets interrupted
python3 auditor_v2.py --resume --key YOUR_KEY --entity "..."
The tool saves progress automatically. Resume picks up exactly where it left off and retries only the services that failed.

Screenshots
Add --screenshots to capture a screenshot of each issue's location on the page. Add --drive-key and --drive-folder to upload them to Google Drive automatically.
python3 auditor_v2.py \
  --psid 3182 \
  --key YOUR_KEY \
  --entity "Ministry" \
  --screenshots \
  --drive-key client_secret.json \
  --drive-folder YOUR_FOLDER_ID

Review issues before exporting
Add --reviewer to enter an interactive review after the audit:
python3 auditor_v2.py --psid 3182 --key YOUR_KEY --reviewer
For each issue you can accept it, edit the description or solution, reject it as a false positive, or skip it. You can also add issues the tool missed. The final CSV contains only the issues you approved.
You can also review an existing CSV directly:
python3 hitl_reviewer.py --csv reports/Ministry_3182.csv --entity "Ministry"

Web interface
streamlit run app.py
The web interface lets you upload HTML files or enter service IDs, choose your audit mode and API providers, enable screenshots and Drive upload, and review issues before downloading — all without touching the command line.

File structure
multiagent/
├── auditor_v2.py       Main CLI
├── pipeline.py         Parallel processing engine
├── rules.py            Rule engine (30+ checks)
├── ai.py               AI prompts and provider routing
├── qa_agent.py         Pre-detection flags + audit orchestration
├── scraper.py          Page scraping
├── output.py           CSV generation
├── screenshot.py       Screenshot capture
├── drive.py            Google Drive upload
├── cache.py            Content-hash cache (skip unchanged pages)
├── hitl_reviewer.py    Terminal review tool
├── app.py              Streamlit web interface
│
└── capstone/
    ├── annotate.py     Build and label ground truth dataset
    ├── evaluate.py     Measure precision, recall, F1
    ├── visualize.py    Generate evaluation report
    ├── distill.py      Knowledge distillation pipeline
    └── README.md       Capstone documentation

Evaluation (capstone)
Step 1 — Import your reviewed audit CSVs as ground truth:
python3 capstone/annotate.py import \
  --input reports/sheets/ \
  --output ground_truth.csv
Step 2 — Check the dataset:
python3 capstone/annotate.py stats --file ground_truth.csv
Step 3 — Run the evaluation:
python3 capstone/evaluate.py \
  --gt ground_truth.csv \
  --rules-dir reports/eval/rules/ \
  --hybrid-dir reports/eval/hybrid/ \
  --output-dir reports/
Step 4 — Generate the report:
python3 capstone/visualize.py \
  --summary reports/eval_summary_*.json
Opens capstone_report.html with precision, recall, F1 charts by mode and issue type.

Distillation (capstone)
Prepare training data from your audit CSVs:
python3 capstone/distill.py prepare \
  --csv-dir reports/sheets/ \
  --output distilled_output/training_data.jsonl
Run Level 1 — few-shot distillation with llama:
python3 capstone/distill.py level1 \
  --csv-dir reports/sheets/ \
  --groq-key YOUR_GROQ_KEY \
  --n-services 10 \
  --output distilled_output/
Level 2 — fine-tune on Google Colab: Upload training_data.jsonl to Colab, set runtime to T4 GPU, and run the training cells. Takes under 10 minutes for 135 examples.

API keys — where to get them free
Provider	Where	Starts with
Gemini	aistudio.google.com	AIza
Groq	console.groq.com	gsk_
OpenRouter	openrouter.ai	sk-or-

