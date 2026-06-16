---
name: lead-list-crm
description: Build a targeted contact list with Deepline and/or Crustdata, enrich it with emails and LinkedIn handles, then send paced LinkedIn connection requests and manage the whole pipeline in a barebones local web CRM (two buttons — Send Invites N, Update CRM). Use whenever someone wants to go from "find me companies/people who match X" to a working outreach list they can run invites from and track accepts/replies/follow-ups. Not niche-specific — works for any ICP.
---

# Lead List → CRM

End-to-end recipe to (1) **build** a contact list, (2) **enrich** it with emails + LinkedIn member ids, (3) **send** paced LinkedIn connection requests, and (4) **manage** it in a tiny local web CRM with two buttons. Each stage writes a CSV; the CSV is the only state. Keep everything in one working folder.

Assets in this skill:
- `assets/crm_app.py` — the two-button web CRM (Send invites N · Update CRM).
- `assets/prepare_crm.py` — converts any contacts CSV into the CRM schema.

Pick the tools per stage; you don't need all of them. Crustdata alone can do build+enrich; Deepline is great for CSV/play workflows; Unipile drives LinkedIn.

---

## Prereqs

- **Crustdata MCP** (company/people search + enrich). Check balance first: `crustdata_credits_check` (free).
- **Unipile** for LinkedIn actions. Creds live in `~/.config/unipile/.env`:
  ```
  UNIPILE_API_KEY=...
  UNIPILE_DSN=https://apiNN.unipile.com:PORT
  ```
  Load them before any unipile/script call: `set -a; . ~/.config/unipile/.env; set +a`, then verify `unipile accounts list`. A `401 missing_credentials` means the token/DSN is wrong — get a fresh token + matching DSN from dashboard.unipile.com (and confirm a LinkedIn account is actually connected).
- **Deepline** (optional). `deepline auth status`; for CSV viewing use `deepline csv render`. Note the legacy Python CLI ≠ the SDK CLI — the `deepline-plays` skill targets the SDK one.

---

## Stage 1 — Build the list

Goal: a CSV with at least `name, company`. Two good paths:

**Crustdata (fastest for ICP search):**
1. `crustdata_company_search_db` — filter by industry, headcount, location, funding → company set.
2. `crustdata_company_identify` (free, batches up to 25 names comma-separated) → resolve company ids/domains.
3. `crustdata_people_search_db` (3 credits/100) — filter `current_employers.name` + `current_employers.title` + `current_employers.seniority_level` to get the role-holders. Returns `linkedin_profile_url` (often the hashed `/in/ACoAA…` form, which still resolves).
   - Title fields are a **closed vocabulary** for `=`/`in`; use `[.]` substring for free text, and `crustdata_autocomplete_person` to find exact values.
   - For a leadership sweep: title OR of `["CEO","Founder","Chief","Head of","VP","Director"]` + seniority `in ["CXO","Vice President","Director","Owner / Partner"]`.

**Deepline (CSV-native / plays):** use the `deepline-gtm` or `deepline-plays` skills to search companies/people or process an input CSV. Export the run to a CSV.

For a big fan-out (many companies × many titles), spawn one search agent per company in parallel (Agent tool or a Workflow) and concatenate the results — far faster than looping.

Output of this stage: `contacts.csv` (name, company, role, linkedin_url).

## Stage 2 — Enrich

**Emails (Crustdata):**
- `crustdata_people_enrich` with `linkedin_profile_url` (comma-separate up to 25 per call) + `include_business_email: true` (+1 credit each). Returns `business_email` and the **vanity** `linkedin_flagship_url`.
- `include_personal_contact_info: true` (+2 credits) adds personal email/phone but is **plan-gated** — it 403s on many accounts *and still burns credits on the failed call*. Test the flags on ONE profile before running the batch.
- For 26+ profiles / max fill rate, `crustdata_batch_people_enrich` exists but is also access-gated (403 → ping gtm@crustdata.co).

**LinkedIn handle + connection degree (Unipile):** for each contact, `GET /api/v1/users/{member_id}?account_id=…` returns `public_identifier` (→ clean `linkedin.com/in/<slug>`), `headline`, `location`, and `network_distance` (`FIRST/SECOND/THIRD_DEGREE`). `member_id` = the part after `/in/` in the Crustdata URL. Pace ~1s/call; this is how you learn who's already 1st-degree vs reachable.

## Stage 3 — Prepare the CRM file

Convert whatever you built into the CRM schema:
```
python <skill>/assets/prepare_crm.py contacts.csv crm.csv
```
It maps common header aliases and extracts `member_id` from the LinkedIn URL. Required columns it produces:
`person_name, company, role, member_id, linkedin, invited, invited_date, invite_status, messaged, replied, last_touch, next_followup, notes`.
Rows without a `member_id` stay in the list but can't be invited until you resolve one.

## Stage 4 — Run & manage the CRM

```
set -a; . ~/.config/unipile/.env; set +a
nohup python <skill>/assets/crm_app.py > crm_app.log 2>&1 &
# open http://127.0.0.1:8787
```
Env knobs: `CRM_CSV` (default `crm.csv`), `CRM_PORT` (8787), `CRM_TZ` (default `America/Los_Angeles`), `UNIPILE_ACCOUNT_ID` (else auto-detects the first connected LinkedIn account).

**Button 1 — Send invites N**: sends connection requests to the next **N uninvited** rows (those with a `member_id`), via `POST /api/v1/users/invite` (`{provider_id, account_id}`). Paced with **random 35–75s** gaps + a **150–240s cooldown every 10** to avoid tripping LinkedIn's automation alarms. Runs in a background thread; marks `invited=yes` + date as it goes.

**Button 2 — Update CRM**: re-checks each invited row — `network_distance` (accept → `invite_status=accepted`), scans `/api/v1/chats` (`attendee_provider_id`) to flag who you `messaged`, reads chat messages (`is_sender==0` ⇒ inbound) to flag `replied`, then recomputes `next_followup`:
- replied → **today** (HOT, respond now)
- accepted, not messaged → **today** (send first touch)
- accepted, messaged, no reply → **+3 days**
- unaccepted invite → **no follow-up** (just keeps getting re-checked for accepts)

Dates display human-readable (`Wed, 17 Jun`) in the chosen timezone; ISO is kept under the hood for sorting.

## Follow-up reminders

The CRM's `next_followup` column is the durable source of truth. For an active nudge, schedule a one-shot reminder (CronCreate, `recurring:false`, `durable:true`) a few days out whose prompt = "load unipile env, cd to the folder, re-run the CRM update, report new accepts / replies (HOT) / due follow-ups, ask before sending." In-session crons die when the session ends — for a hard backup, add a calendar event.

---

## Gotchas learned the hard way

- **Deepline CSV viewer caches by file path.** After editing a CSV, `deepline csv render`/`csv show` keep serving the *old* version (and restarting the renderer can flush its cached copy back over your edits). Fix: render under a **fresh filename**, and stop the renderer before editing a file it holds.
- **Crustdata charges for failed enrich calls** that include a gated flag (`include_personal_contact_info`). Test flags on one profile first.
- **`crustdata_people_search_db` returns 0 silently** when a closed-vocab value is slightly wrong (`"VP"` vs `"Vice President"`). Use autocomplete or `[.]` substring.
- **Hashed `/in/ACoAA…` URLs work** for invites and `users` lookups; they resolve to the real profile and Unipile returns the vanity slug on enrich.
- **LinkedIn invite ceiling** is ~100–200/week. 50 paced in a run is assertive; let a big batch breathe a few days before the next.
- **`already_invited_recently` (422)** on invite just means a pending invite already exists — count it as skipped, not failed.
- **Accepts trickle in over 3–7 days** — re-run Update over several days, don't judge accept rate on day one.
