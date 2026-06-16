#!/usr/bin/env python3
"""Turn any contacts CSV into the crm.csv schema the CRM app expects.

Usage:
  python prepare_crm.py INPUT.csv [OUTPUT.csv]

INPUT must have a name column and a LinkedIn URL column. The script is forgiving
about header names — it looks for the first matching alias below.

  name      : person_name | name | full_name | founder_name
  company   : company | organization | employer
  role      : role | role_bucket | title
  linkedin  : clean_linkedin_url | linkedin_url | linkedin | profile_url

member_id is auto-extracted from a linkedin.com/in/<id> URL (works for both
the hashed ACoAA… provider id and a vanity slug; the hashed id is what the
Unipile invite endpoint needs). Rows with no extractable id are kept but can't
be invited until you resolve one.
"""
import csv, sys, os

ALIASES = {
    "person_name": ["person_name","name","full_name","founder_name"],
    "company":     ["company","organization","org","employer"],
    "role":        ["role","role_bucket","title"],
    "linkedin":    ["clean_linkedin_url","linkedin_url","linkedin","profile_url","li_url"],
}
COLS = ["person_name","company","role","member_id","linkedin","invited","invited_date",
        "invite_status","messaged","replied","last_touch","next_followup","notes"]

def pick(row, aliases):
    for a in aliases:
        for k in row:
            if k.strip().lower() == a:
                return row[k].strip()
    return ""

def member_id(url):
    if not url or "/in/" not in url: return ""
    return url.rstrip("/").split("/in/")[-1]

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python prepare_crm.py INPUT.csv [OUTPUT.csv]")
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "crm.csv"
    rows = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            li = pick(r, ALIASES["linkedin"])
            rows.append({
                "person_name": pick(r, ALIASES["person_name"]),
                "company":     pick(r, ALIASES["company"]),
                "role":        pick(r, ALIASES["role"]),
                "member_id":   member_id(li),
                "linkedin":    li,
                "invited":"no","invited_date":"","invite_status":"",
                "messaged":"no","replied":"no","last_touch":"","next_followup":"","notes":"",
            })
    rows = [r for r in rows if r["person_name"]]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    invitable = sum(1 for r in rows if r["member_id"])
    print(f"wrote {out}: {len(rows)} contacts, {invitable} invitable (have member_id)")

if __name__ == "__main__":
    main()
