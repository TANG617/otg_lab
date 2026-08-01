PYTHON ?= ../.venv/bin/python
PROFILE ?= provisional
LATEX_COMPILE ?= /Users/timli/.codex/plugins/cache/openai-bundled/latex/0.2.4/scripts/compile_latex.py
PAPER_ROOT := $(abspath .)

.PHONY: evidence artifacts validate texlive tectonic draft check arxiv-dry-run release arxiv

evidence:
	$(PYTHON) scripts/freeze_evidence.py --profile $(PROFILE)

artifacts: evidence
	$(PYTHON) scripts/build_artifacts.py --profile $(PROFILE)

validate: artifacts
	$(PYTHON) scripts/validate_paper.py --profile $(PROFILE)

texlive: validate
	mkdir -p build/texlive
	python3 $(LATEX_COMPILE) $(PAPER_ROOT)/main.tex --compiler texlive --output-directory $(PAPER_ROOT)/build/texlive

tectonic: validate
	mkdir -p build/tectonic
	python3 $(LATEX_COMPILE) $(PAPER_ROOT)/main.tex --compiler tectonic --output-directory $(PAPER_ROOT)/build/tectonic

draft: texlive tectonic
	mkdir -p ../output/pdf
	cp build/texlive/main.pdf ../output/pdf/terminal_state_mismatch_provisional.pdf

check: draft
	$(PYTHON) -m pytest -q tests/test_paper_artifacts.py
	$(PYTHON) scripts/validate_paper.py --profile $(PROFILE) --pdf build/texlive/main.pdf

arxiv-dry-run: check
	$(PYTHON) scripts/package_arxiv.py --dry-run --output $(PAPER_ROOT)/dist/review-source-dry-run.tar.gz
	python3 $(LATEX_COMPILE) $(PAPER_ROOT)/build/arxiv-dry-run/main.tex --compiler texlive --output-directory $(PAPER_ROOT)/build/arxiv-dry-run/build

release:
	$(PYTHON) scripts/freeze_evidence.py --profile release --require-release
	$(PYTHON) scripts/build_artifacts.py --profile release
	$(PYTHON) scripts/validate_paper.py --profile release --release

arxiv: release
	python3 $(LATEX_COMPILE) $(PAPER_ROOT)/main.tex --compiler texlive --output-directory $(PAPER_ROOT)/build/texlive
	$(PYTHON) scripts/package_arxiv.py --output $(PAPER_ROOT)/dist/arxiv-source.tar.gz
