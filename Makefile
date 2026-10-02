PY ?= python3

.PHONY: demo test restart-check scan

demo:
	$(PY) demo.py

test:
	$(PY) -m pytest -q

restart-check:
	$(PY) tools/restart_check.py

scan:
	gitleaks dir . --no-banner
	gitleaks git . --no-banner
