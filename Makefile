PY ?= python3

.PHONY: demo test hero verify clean

demo:            ## cohort 1 -> SIMULATED feedback -> cohort 2, offline, $0
	$(PY) -m ugcm demo --out out

test:            ## all offline tests
	$(PY) -m unittest discover -s tests -t . -v

verify:          ## re-hash every asset against the ledgers
	$(PY) -m ugcm verify --cohort out/cohort-001
	$(PY) -m ugcm verify --cohort out/cohort-002

hero: demo       ## render docs/hero.png and docs/contact-sheet.png from the demo output (needs Chrome)
	$(PY) scripts/render_hero.py --demo out/demo-summary.json --out docs/hero.png
	cp out/cohort-001/contact-sheet.png docs/contact-sheet.png

clean:
	rm -rf out
