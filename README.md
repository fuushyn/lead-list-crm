# lead-list-crm

**Go from "find me people who match X" to a running, trackable outreach CRM.**

A reusable [Agent Skill](https://agentskills.io) that wires together **Crustdata** (find + enrich), **Deepline** (CSV/play workflows), and **Unipile** (LinkedIn) into a four-stage pipeline, then hands you a **barebones local web CRM with two buttons**:

- **Send invites N** — sends LinkedIn connection requests to the next *N* uninvited contacts, paced with random waits to stay under automation radar.
- **Update CRM** — re-checks who accepted, who you messaged, who replied, and tells you who to follow up with next.

Not niche-specific — works for any ICP. The runtime spec lives in [`skills/lead-list-crm/SKILL.md`](skills/lead-list-crm/SKILL.md), the source of truth.

## Install

**Claude Code (recommended — auto-updates via marketplace):**
```
/plugin marketplace add fuushyn/lead-list-crm
/plugin install lead-list-crm
```

**Codex, Cursor, Copilot, Gemini CLI, or any of 50+ [Agent Skills](https://agentskills.io) hosts:**
```
npx skills add fuushyn/lead-list-crm -g
```
(`-g` installs globally for your user; drop it to scope per-project.)

**Manual:** copy `skills/lead-list-crm/` into your host's skills directory (e.g. `~/.claude/skills/lead-list-crm/` for Claude Code).

## What it does

| Stage | Tool(s) | Output |
|-------|---------|--------|
| 1. Build | Crustdata `company_search_db → company_identify → people_search_db`, or Deepline plays | `contacts.csv` (name, company, role, linkedin) |
| 2. Enrich | Crustdata `people_enrich` (business email), Unipile `users` (clean handle + connection degree) | enriched contacts |
| 3. Prepare | `prepare_crm.py` | `crm.csv` (the CRM schema) |
| 4. Manage | `crm_app.py` | the two-button web CRM |

## Prerequisites

- **Crustdata** MCP (company/people search + enrichment).
- **Unipile** for LinkedIn — creds in `~/.config/unipile/.env` (`UNIPILE_API_KEY`, `UNIPILE_DSN`).
- **Deepline** (optional) for CSV-native list building / viewing.
- Python 3.9+ (standard library only; no dependencies).

## Run the CRM directly

```bash
set -a; . ~/.config/unipile/.env; set +a
python skills/lead-list-crm/assets/prepare_crm.py contacts.csv crm.csv
python skills/lead-list-crm/assets/crm_app.py     # open http://127.0.0.1:8787
```
Env knobs: `CRM_CSV`, `CRM_PORT` (8787), `CRM_TZ` (default `America/Los_Angeles`), `UNIPILE_ACCOUNT_ID` (else auto-detected).

## License

MIT — see [LICENSE](LICENSE).
