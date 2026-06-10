# Job Hunter 🎯

**Agentic Job Search Workflow — OpenClaw Agent**

> An AI agent that hunts remote AI engineering jobs, filters them in 2 deterministic layers, calculates a CV match rate, and notifies via Telegram for validation before generating ATS-optimized CVs and cover letters.

---

## What it does

The Hunter is an OpenClaw-based AI agent running on a self-hosted VPS (n8n + systemd). Every day at 12h00 it:

1. **Scans** Indeed (MCP) + LinkedIn (Playwright) for remote AI/ML/FullStack job postings — 60 + 40 offers/day
2. **Deduplicates** permanently — nothing ever comes back once seen
3. **Filters** in 2 deterministic layers (zero tokens):
    - Layer 1: disqualifying filters (on-site, junior, excluded sectors)
    - Layer 2: CV match rate against `SKILLS_MASTER.md` — threshold ≥ 60%
4. **Notifies** via Telegram — max 25/day, sorted by match rate descending
    - ≥ 80% → immediate individual notification
    - 60-79% → daily digest at 12h30
5. **Generates** on Patricia's validation: ATS-optimized CV + cover letter in the offer's language (EN/FR/DE)
6. **Applies** directly via Playwright on Easy Apply (Indeed/LinkedIn) on Patricia's confirmation
7. **Detects** form questions on company websites and generates tailored answers
8. **Tracks** everything in Excel Google Drive (3 tabs: MATCHES · SCANNED_HASHES · PENDING_MATCHES)

---

## Architecture

```
12h00 — n8n Scheduler (VPS systemd)
        |
        ▼
Read PENDING_MATCHES + SCANNED_HASHES (Excel Drive)
        |
        ▼
Scan: 60 Indeed Playwright + 40 LinkedIn Playwright
        |
        ▼
Layer 1 — Disqualifying filters (Python, zero tokens)
on-site · junior · excluded sectors
Hybrid tolerated: Valais 🇨🇭 · Nouvelle-Aquitaine 🇫🇷
        |
        ▼
Layer 2 — CV Match Rate (Python, zero tokens)
SKILLS_MASTER.md + alias table
< 60% → silent log | ≥ 60% → qualify
        |
        ▼
Write ALL scanned → SCANNED_HASHES
Write ≥ 60% → MATCHES or PENDING_MATCHES
        |
        ▼
12h30 — Telegram Notifications
≥ 80% → immediate individual
60-79% → digest sorted descending · max 25/day
        |
  Patricia validates
        ▼
Claude Haiku → offer analysis
Claude Sonnet → CV rewriter + Cover letter
(CV_BASE.md · SKILLS_MASTER.md · GITHUB_REPOS.md · Voice Profile)
        |
        ▼
Google Drive storage + Excel MATCHES status update
        |
  Easy Apply detected?
  YES → Playwright applies on Patricia's confirmation
  NO  → Playwright detects form questions → Claude Haiku answers
```

---

## File Structure

|File|Purpose|
|---|---|
|`CV_BASE.md`|Master CV with tagged sections by profile (ai_engineer / ai_builder / full_stack / mlops)|
|`SKILLS_MASTER.md`|Full keyword list for match rate calculation + alias table|
|`GITHUB_REPOS.md`|Public/private repos + hackathons — injected in CV/letter generation|
|`USER.md`|Patricia's profile, pre-calibrated answers, geo tolerances|
|`SOUL.md`|Agent personality, Voice Profile, output rules|
|`TOOLS.md`|Tool definitions|
|`HEARTBEAT.md`|Schedule (12h00 daily)|
|`KEYWORDS.md`|Scan root queries|
|`ARCHITECTURE-VPS.md`|Full VPS deployment architecture|

---

## Matching System

**2-layer deterministic pipeline — zero tokens:**

**Layer 1 — Hard filters (binary)**

|Criterion|Rule|
|---|---|
|On-site|Always rejected|
|Hybrid (except Valais CH + Nouvelle-Aquitaine FR)|Rejected|
|Junior / internship / alternance|Rejected|
|Gambling / weapons / tobacco|Rejected|

**Layer 2 — CV Match Rate**

```
Match Rate = keywords found in SKILLS_MASTER / total keywords in offer × 100
```

Alias table handles vocabulary differences: "Vector DB" → Qdrant/Pinecone/pgvector, "GenAI" → LLM, etc.

|Threshold|Action|
|---|---|
|< 60%|Silent log in SCANNED_HASHES only|
|≥ 60%|Logged in MATCHES + eligible for notification|
|≥ 80%|Immediate individual Telegram notification|
|≥ 80% (Phase 2)|Auto Easy Apply with 2h veto window|

---

## Token Usage — Deterministic First

|Task|Method|Model|
|---|---|---|
|Layer 1 filters|Python regex|None|
|Match rate calculation|Python set intersection|None|
|Deduplication|SHA-256 + Excel lookup|None|
|Sorting + top 25 cap|Python|None|
|Form question detection|Playwright + regex|None|
|Pre-calibrated answers|USER.md lookup|None|
|Offer analysis (Telegram card)|LLM|**Haiku**|
|Form question answers|LLM|**Haiku**|
|CV rewriting (ATS)|LLM|**Sonnet**|
|Cover letter generation|LLM|**Sonnet**|

**Estimated cost: ~$0.10/active day. Zero Sonnet tokens on days Patricia doesn't validate.**

---

## Phases

|Phase|Trigger|Behavior|
|---|---|---|
|1 — Supervised (current)|Launch|Patricia validates every generation and application|
|2 — Semi-autonomous|~20 validated offers|Easy Apply auto on Patricia's Telegram confirmation|
|3 — Autonomous|Calibration confirmed|Full auto on Indeed · Weekly report only|

LinkedIn: **never auto-apply** (bot detection risk — account ban).

---

## Scan Sources

|Platform|Method|Volume|Status|
|---|---|---|---|
|Indeed|Playwright scraper|60/day|✅ V1|
|LinkedIn|Playwright scraper|40/day|✅ V1|
|Collective Work|API or scraping|TBD|🔲 V2|

**Scan root queries (12 roots, remote filter applied globally):** `AI` · `agent` · `agentic` · `GenAI` · `automation` · `LLM` · `RAG` · `ML` · `full stack` · `n8n` · `Python` · `developer`

---

## Built with

- [OpenClaw](https://openclaw.ai/) — Agent framework
- [Claude API](https://anthropic.com/) — CV rewriting & cover letters (Haiku + Sonnet)
- [n8n](https://n8n.io/) — Workflow orchestration (self-hosted VPS)
- Indeed — Job search via Playwright scraper (`indeed_scraper.py`; MCP déprécié)
- [Google Drive MCP](https://drivemcp.googleapis.com/) — Document + tracker storage
- [Telegram Bot API](https://core.telegram.org/bots) — Notifications & validation
- [Playwright](https://playwright.dev/) — LinkedIn scraping + Easy Apply + form detection

---

## Tracking — Excel Google Drive

**File:** `job-hunter-tracker.xlsx`

|Tab|Content|
|---|---|
|`MATCHES`|All offers ≥ 60% with full details, status, Drive links|
|`SCANNED_HASHES`|All scanned offers (100/day) — SHA-256 + date. Permanent deduplication|
|`PENDING_MATCHES`|Offers ≥ 60% not yet notified (rank 26+) — processed first next day|

---

## Read the story

_Coming soon on Medium: "I built an AI agent to hunt my own job — here's exactly how"_

---

_Built by Patricia Wintrebert — AI Product Builder & Agentic Workflow Engineer_ _linkedin.com/in/patriciawintrebert · github.com/patw47_
