.PHONY: install run-daily run-weekly

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-daily:
	.venv/bin/python daily_progress_agent.py \
	  --date $$(date +%Y-%m-%d) \
	  --auto-scan \
	  --scan-limit 20

run-weekly:
	.venv/bin/python weekly_report_agent.py
