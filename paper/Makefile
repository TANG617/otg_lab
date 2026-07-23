PYTHON ?= uv run python
LATEXMK ?= $(shell command -v latexmk 2>/dev/null || find "$${HOME}/.cache/codex-runtimes/codex-texlive/full/bin" -name latexmk -perm -111 -print -quit 2>/dev/null)
BUILD_DIR ?= build
MAIN ?= main.tex

ifeq ($(strip $(LATEXMK)),)
LATEXMK := latexmk
endif

LATEX_BIN := $(patsubst %/,%,$(dir $(LATEXMK)))
export PATH := $(LATEX_BIN):$(PATH)

.DEFAULT_GOAL := help

.PHONY: help logic-check logic-lock evidence evidence-check figures tables pdf \
	check static-check local-path-check arxiv-source prism-package clean

help:
	@echo "Paper targets:"
	@echo "  logic-check    validate the logic layer and its content-addressed lock"
	@echo "  logic-lock     validate and rebuild logic/logic_lock.json"
	@echo "  evidence       extract bounded evidence and generate numeric macros"
	@echo "  figures        generate source-backed PDF figures"
	@echo "  tables         generate source-backed LaTeX tables"
	@echo "  pdf            build build/main.pdf with PDFLaTeX, BibTeX, and natbib"
	@echo "  static-check   run non-compiling source and provenance checks"
	@echo "  check          run the complete manuscript, package, and clean-build QA"
	@echo "  arxiv-source   create and clean-build the arXiv stage source ZIP"
	@echo "  prism-package  create the milestone Prism import ZIP and manifest"
	@echo "  clean          remove local LaTeX and package build products"

logic-check:
	$(PYTHON) scripts/check_logic.py
	$(PYTHON) scripts/build_logic_lock.py --verify

logic-lock:
	$(PYTHON) scripts/check_logic.py
	$(PYTHON) scripts/build_logic_lock.py

evidence:
	$(PYTHON) scripts/extract_evidence.py
	$(PYTHON) scripts/generate_numbers.py
	$(PYTHON) scripts/build_generation_manifest.py

evidence-check:
	$(PYTHON) scripts/extract_evidence.py --check
	$(PYTHON) scripts/generate_numbers.py --check
	$(PYTHON) scripts/generate_tables.py --check
	$(PYTHON) scripts/generate_figures.py --check
	$(PYTHON) scripts/verify_v3_immutability.py

figures: evidence
	$(PYTHON) scripts/generate_figures.py
	$(PYTHON) scripts/build_generation_manifest.py

tables: evidence
	$(PYTHON) scripts/generate_tables.py
	$(PYTHON) scripts/build_generation_manifest.py

pdf: figures tables
	$(LATEXMK) -pdf -outdir=$(BUILD_DIR) $(MAIN)
	@test -f $(BUILD_DIR)/main.pdf
	@test -f $(BUILD_DIR)/main.bbl
	@cp $(BUILD_DIR)/main.bbl main.bbl

local-path-check:
	@if rg -n --glob '*.tex' --glob '*.bib' --glob '*.md' \
		'(/Users/|/home/|[A-Za-z]:\\\\Users\\\\)' . \
		--glob '!dist/**' --glob '!build/**'; then \
		echo "ERROR: local absolute path found in paper source"; \
		exit 1; \
	fi

static-check: logic-check evidence-check local-path-check
	$(PYTHON) scripts/check_claims.py
	$(PYTHON) scripts/check_numbers.py
	$(PYTHON) scripts/check_citations.py

arxiv-source: pdf
	$(PYTHON) scripts/package_arxiv.py

prism-package: pdf
	$(PYTHON) scripts/package_prism.py

check: static-check pdf
	$(PYTHON) scripts/check_latex_log.py $(BUILD_DIR)/main.log
	$(PYTHON) scripts/package_arxiv.py
	$(PYTHON) scripts/package_prism.py
	@test -f dist/arxiv_stage_source_v0.manifest.json
	@test -f dist/prism_import_v0.manifest.json

clean:
	-$(LATEXMK) -C -outdir=$(BUILD_DIR) $(MAIN)
	rm -rf "$(BUILD_DIR)" dist
