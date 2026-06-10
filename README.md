# Databases in the Age of AI

[![lab-materials CI](https://github.com/zozo123/databases-in-the-ai-era/actions/workflows/labs.yml/badge.svg)](https://github.com/zozo123/databases-in-the-ai-era/actions/workflows/labs.yml)

Two essays and a complete, free graduate database course for the agentic era. All four labs' starter materials are CI-tested on every push (cargo test, go vet/build, DuckDB schema + 50 gold queries + grader self-test, deterministic data-gen + operator smoke test).

**Start here:** https://zozo123.github.io/databases-in-the-ai-era/

## Essay № 01 — [Databases in the Age of AI](https://zozo123.github.io/databases-in-the-ai-era/)

Seven chapters on what happens to the fifty-year-old discipline of database systems when the dominant reader of data is no longer human: the Anthropic self-service analytics findings (21% → 95% accuracy with curated context), the *Database Internals* (Petrov) lens, agent-native databases, the catalog wars, a stress-test of the "Databricks explodes" thesis, and ten dated, falsifiable predictions for 2027–2034.

## Essay № 02 — [The 2027 Syllabus](https://zozo123.github.io/databases-in-the-ai-era/course.html)

Field notes on what the real flagship courses (MIT 6.5830, Harvard CS265, CMU 15-445/721, Stanford, Berkeley) actually teach in 2025–26 — and the design for the course none of them teach yet.

## The course — [DATA 2027: Data Systems in the Agentic Era](https://zozo123.github.io/databases-in-the-ai-era/course/)

Complete open courseware:

- **14 weeks of lecture notes** (~53k words): two lectures per week with worked math, SVG figures, field notes, readings, and exercises
- **14 slide decks** (423 slides): keyboard-navigable single-file HTML, generated from the notes; `p` prints
- **4 systems labs with real, tested starter materials:**
  - Lab 1 — VLSM, an LSM-tree with vector segments (Rust skeleton; `cargo test` passing)
  - Lab 2 — Mini-Neon, copy-on-write pages over object storage (Go kit; `go vet` clean; deterministic 50-branch WAL workload)
  - Lab 3 — Text-to-SQL agent + eval harness (126-table trap schema validated in DuckDB, 50 gold-SQL questions, Cube-style semantic layer)
  - Lab 4 — Semantic-operator optimizer (sealed deterministic mock-LLM simulator with cost ledger; naive plan $205,742 vs reference $636)
- **[Final projects](https://zozo123.github.io/databases-in-the-ai-era/course/projects.html)** — 8 publishable project briefs · **[Synthesis exam](https://zozo123.github.io/databases-in-the-ai-era/course/exam.html)** — 5 questions, open-everything including frontier models
- **[Instructor's guide](https://zozo123.github.io/databases-in-the-ai-era/course/instructors.html)** — pacing variants, lab ops, LLM-policy enforcement · **[Resources](https://zozo123.github.io/databases-in-the-ai-era/course/resources.html)** — all 42 readings by part

## Provenance

Researched and written by ~45 parallel AI agents across four workflow waves, synthesized and quality-controlled by one; all sources cited in margins, vendor-reported figures flagged, every code artifact machine-tested before publishing. Instructors should review before teaching — PRs welcome.

Single-purpose static site, zero dependencies. Type: Fraunces · Newsreader · IBM Plex Mono.

License: CC BY 4.0 · Built with [Claude Code](https://claude.com/claude-code)
