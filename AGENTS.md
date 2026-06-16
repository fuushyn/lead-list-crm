# lead-list-crm

Agent Skills package for building and managing an outreach list end to end. Installable across Claude Code (most common host), Codex, Cursor, GitHub Copilot, Gemini CLI, and 50+ other [Agent Skills](https://agentskills.io) hosts. Pure-Python (stdlib only), no dependencies.

## Structure
- `skills/lead-list-crm/SKILL.md` — canonical skill definition / runtime spec the model reads (the source of truth for the four-stage pipeline and gotchas).
- `skills/lead-list-crm/assets/crm_app.py` — the two-button local web CRM (Send invites N · Update CRM) over a CSV.
- `skills/lead-list-crm/assets/prepare_crm.py` — converts any contacts CSV into the CRM schema.
- `skills/lead-list-crm/agents/openai.yaml` — Codex/OpenAI display + invocation metadata.
- `.claude-plugin/` — Claude Code plugin + marketplace manifests (`/plugin marketplace add fuushyn/lead-list-crm`).
- `.agents/plugins/marketplace.json` — Agent Skills marketplace manifest.

## Orientation
- This is an Agent Skills **package**, not a CLI tool. The product is the skill the model reads (`SKILL.md`); the two Python files are implementation assets it invokes. Features must work across every host the skill installs into.
- The pipeline is build → enrich → prepare → manage. Each stage is a CSV boundary; the CSV is the only state. `crm_app.py`'s CSV (`crm.csv`) is the durable CRM.
- Credentials are never stored in the repo. Unipile creds load from `~/.config/unipile/.env`; Crustdata runs via the host's MCP. `.gitignore` keeps `crm.csv`, logs, and tmp files out of version control so real contact data is never committed.

## Commands
```bash
# prepare the CRM file from any contacts CSV
python skills/lead-list-crm/assets/prepare_crm.py contacts.csv crm.csv
# run the two-button CRM (needs Unipile env loaded)
set -a; . ~/.config/unipile/.env; set +a
python skills/lead-list-crm/assets/crm_app.py        # http://127.0.0.1:8787
# install/sync into a host (frozen copy; re-run after edits)
npx skills add . -g -y
```
Python 3.9+; standard library only.

## Rules
- No third-party Python deps — keep `crm_app.py` / `prepare_crm.py` stdlib-only so they run anywhere.
- Never hardcode credentials or account ids; `crm_app.py` reads env and auto-detects the LinkedIn account.
- LinkedIn invites are rate-sensitive: keep the randomized pacing (35–75s + cooldown every 10) and the ~100–200/week ceiling guidance intact.
- Git remote: origin = private (`fuushyn/lead-list-crm`).
