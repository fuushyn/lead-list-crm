#!/usr/bin/env python3
"""Barebones outreach CRM — a local web app with two buttons over a CSV "list".

  Button 1  Send invites N   -> sends LinkedIn connection requests to the next N
                               UNINVITED rows, paced with random waits.
  Button 2  Update CRM        -> re-checks accepts (network distance), who you
                               messaged, who replied, and recomputes next_followup.

Data store = a single CSV (the "list" primitive). Generic: not tied to any niche.

Env:
  UNIPILE_DSN, UNIPILE_API_KEY   (required; from ~/.config/unipile/.env)
  UNIPILE_ACCOUNT_ID             (optional; auto-detected from first LinkedIn account)
  CRM_CSV                        (optional; default ./crm.csv)
  CRM_PORT                       (optional; default 8787)
  CRM_TZ                         (optional; default America/Los_Angeles)

Required CSV columns:
  person_name, company, role, member_id, linkedin,
  invited, invited_date, invite_status, messaged, replied,
  last_touch, next_followup, notes
  (member_id = the LinkedIn provider id, i.e. the part after /in/ in an
   linkedin.com/in/ACoAA... URL. Rows without a member_id can't be invited.)

Run:  python crm_app.py
"""
import os, csv, json, time, random, threading, urllib.request, urllib.error, html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DSN  = os.environ["UNIPILE_DSN"].rstrip("/")
KEY  = os.environ["UNIPILE_API_KEY"]
CSV  = os.environ.get("CRM_CSV", "crm.csv")
PORT = int(os.environ.get("CRM_PORT", "8787"))
TZ   = ZoneInfo(os.environ.get("CRM_TZ", "America/Los_Angeles"))
COLS = ["person_name","company","role","member_id","linkedin","invited","invited_date",
        "invite_status","messaged","replied","last_touch","next_followup","notes"]

STATE = {"busy": False, "msg": "Ready."}
LOCK  = threading.Lock()

def today():  return datetime.now(TZ).date()
def ds(d):    return d.strftime("%Y-%m-%d")
def hfmt(s):                                   # ISO -> "Wed, 17 Jun"
    if not s: return ""
    try: return datetime.strptime(s, "%Y-%m-%d").strftime("%a, %-d %b")
    except Exception: return s

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{DSN}{path}", data=data, method=method,
        headers={"X-API-KEY": KEY, "accept": "application/json", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e: return 0, {"error": str(e)}

def detect_account():
    if os.environ.get("UNIPILE_ACCOUNT_ID"): return os.environ["UNIPILE_ACCOUNT_ID"]
    _, d = api("GET", "/api/v1/accounts")
    for a in d.get("items", []):
        if a.get("type") == "LINKEDIN": return a["id"]
    raise SystemExit("No LinkedIn account connected in Unipile.")
ACCT = detect_account()

def load():
    with open(CSV, newline="") as f: return list(csv.DictReader(f))
def save(rows):
    with open(CSV+".tmp","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=COLS); w.writeheader()
        for r in rows: w.writerow({k:r.get(k,"") for k in COLS})
    os.replace(CSV+".tmp", CSV)

# ---------- ACTION 1: SEND INVITES (paced) ----------
ACCOUNT_ERRS = ("disconnected_account", "missing_credentials", "invalid_credentials", "credentials")

def account_blocked():
    """Return a human reason if the LinkedIn account can't send, else None."""
    st, d = api("GET", f"/api/v1/accounts/{ACCT}")
    if st != 200:
        return f"account check failed (HTTP {st})"
    sts = [s.get("status") for s in d.get("sources", [])]
    if not sts or any(s != "OK" for s in sts):
        return f"not connected (source status: {','.join(sts) or 'unknown'})"
    return None

def do_send(n):
    rows = load()
    # PREFLIGHT: if the LinkedIn account is disconnected, fail the whole run and surface it.
    reason = account_blocked()
    if reason:
        STATE["msg"] = f"❌ ABORTED — LinkedIn account {reason}. Reconnect at dashboard.unipile.com, then retry. No invites sent."
        return
    queue = [r for r in rows if r.get("invited") != "yes" and r.get("member_id")][:n]
    STATE["msg"] = f"Sending {len(queue)} invites (paced)…"
    sent = fail = 0
    for i, r in enumerate(queue, 1):
        st, resp = api("POST", "/api/v1/users/invite", {"provider_id": r["member_id"], "account_id": ACCT})
        etype = (resp or {}).get("type", "")
        # ACCOUNT-LEVEL failure mid-run -> abort everything, mark nothing, surface the error.
        if st == 401 or any(e in etype for e in ACCOUNT_ERRS):
            STATE["msg"] = (f"❌ ABORTED at {i}/{len(queue)} — LinkedIn disconnected "
                            f"({etype or 'HTTP '+str(st)}). {sent} sent before failure; remaining untouched. "
                            f"Reconnect in Unipile, then retry.")
            return
        if st in (200, 201):
            r["invited"]="yes"; r["invited_date"]=ds(today()); r["last_touch"]=ds(today()); r["invite_status"]="pending"; sent += 1
        elif "already_invited" in etype:
            r["invited"]="yes"; r["invited_date"]=ds(today()); r["invite_status"]="already_invited"; sent += 1
        else:
            fail += 1   # per-contact error: leave invited=no so it stays retryable
            r["notes"]=(r.get("notes","")+f" invite_err:{etype or st}").strip()
        save(rows)
        STATE["msg"]=f"Sending… {i}/{len(queue)} ({sent} ok, {fail} fail)"
        if i < len(queue):
            time.sleep(random.uniform(150,240) if i % 10 == 0 else random.uniform(35,75))
    STATE["msg"]=f"Done: sent {sent}, failed {fail}. Run Update CRM in a day or two to track accepts."

# ---------- ACTION 2: UPDATE CRM ----------
def chat_index():
    idx={}; cur=None
    for _ in range(8):
        _, d = api("GET", f"/api/v1/chats?account_id={ACCT}&limit=250"+(f"&cursor={cur}" if cur else ""))
        for c in d.get("items", []): idx[c.get("attendee_provider_id")] = c.get("id")
        cur = d.get("cursor")
        if not cur: break
    return idx

def has_reply(chat_id):
    _, d = api("GET", f"/api/v1/chats/{chat_id}/messages?limit=30")
    return any(m.get("is_sender") in (0, False) for m in d.get("items", []))

def do_update():
    rows = load()
    STATE["msg"]="Fetching chats…"
    chats = chat_index()
    inv = [r for r in rows if r.get("invited")=="yes" and r.get("member_id")]
    for i, r in enumerate(inv, 1):
        STATE["msg"]=f"Updating… {i}/{len(inv)}"
        _, d = api("GET", f"/api/v1/users/{r['member_id']}?account_id={ACCT}")
        nd = d.get("network_distance","")
        r["invite_status"]="accepted" if nd=="FIRST_DEGREE" else (r.get("invite_status") or "pending")
        cid = chats.get(r["member_id"])
        if cid:
            r["messaged"]="yes"
            r["replied"]="yes" if has_reply(cid) else "no"
        time.sleep(0.5)
        # follow-up rules (no follow-up for unaccepted invites)
        if r["replied"]=="yes":
            r["next_followup"]=ds(today()); r["notes"]=("HOT: replied — respond now. "+r.get("notes","")).strip()
        elif r["invite_status"]=="accepted" and r["messaged"]!="yes":
            r["next_followup"]=ds(today())                       # send first message now
        elif r["invite_status"]=="accepted" and r["messaged"]=="yes":
            r["next_followup"]=ds(today()+timedelta(days=3))     # nudge in 3 days
        else:
            r["next_followup"]=""                                # unaccepted -> none
        save(rows)
    acc=sum(1 for r in inv if r["invite_status"]=="accepted")
    rep=sum(1 for r in inv if r.get("replied")=="yes")
    due=sorted([r for r in rows if r.get("next_followup")], key=lambda r:r["next_followup"])
    nxt = hfmt(due[0]["next_followup"]) if due else "—"
    STATE["msg"]=f"Updated {len(inv)} invited. Accepted: {acc} · Replied: {rep}. Next follow-up: {nxt}."

def run_bg(fn, *a):
    def wrap():
        with LOCK:
            STATE["busy"]=True
            try: fn(*a)
            except Exception as e: STATE["msg"]=f"Error: {e}"
            finally: STATE["busy"]=False
    threading.Thread(target=wrap, daemon=True).start()

# ---------- UI ----------
def page():
    rows=load()
    c=lambda f,v: sum(1 for r in rows if r.get(f)==v)
    n_inv=c("invited","yes"); n_acc=c("invite_status","accepted"); n_msg=c("messaged","yes"); n_rep=c("replied","yes")
    n_left=sum(1 for r in rows if r.get("invited")!="yes" and r.get("member_id"))
    due=sorted([r for r in rows if r.get("next_followup")], key=lambda r:r["next_followup"])
    refresh='<meta http-equiv="refresh" content="4">' if STATE["busy"] else ''
    show=sorted(rows, key=lambda r:(0 if r.get("replied")=="yes" else 1,
                                    0 if r.get("invite_status")=="accepted" else 1,
                                    0 if r.get("invited")=="yes" else 2, r.get("company","")))
    trs=[]
    for r in show[:150]:
        badge="✅ replied" if r.get("replied")=="yes" else ("🟢 accepted" if r.get("invite_status")=="accepted" else ("🟡 pending" if r.get("invited")=="yes" else "—"))
        msg="✉️" if r.get("messaged")=="yes" else ""
        li=f'<a href="{html.escape(r.get("linkedin",""))}" target=_blank>link</a>' if r.get("linkedin") else ""
        trs.append(f"<tr><td>{html.escape(r.get('person_name',''))}</td><td>{html.escape(r.get('company',''))}</td><td>{badge}</td><td>{msg}</td><td>{html.escape(hfmt(r.get('next_followup','')))}</td><td>{li}</td></tr>")
    nxt=(html.escape(hfmt(due[0]['next_followup']))+' — '+html.escape(due[0]['person_name'])+' ('+html.escape(due[0]['company'])+')') if due else '— run Update CRM'
    return f"""<!doctype html><html><head><meta charset=utf-8>{refresh}<title>Outreach CRM</title><style>
body{{font:14px -apple-system,system-ui,sans-serif;max-width:1000px;margin:30px auto;padding:0 16px;color:#111}}
.bar{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;font-weight:600}}
.bar span{{background:#f3f4f6;padding:6px 12px;border-radius:8px}}
.actions{{display:flex;gap:12px;align-items:center;margin:18px 0;padding:14px;background:#fafafa;border:1px solid #eee;border-radius:10px}}
button{{background:#111;color:#fff;border:0;padding:9px 16px;border-radius:8px;font-weight:600;cursor:pointer}}
button:disabled{{opacity:.4}} input{{width:64px;padding:8px;border:1px solid #ccc;border-radius:6px}}
.status{{padding:8px 12px;border-radius:8px;background:{'#fff7ed' if STATE['busy'] else '#ecfdf5'};border:1px solid #eee;margin:10px 0}}
table{{border-collapse:collapse;width:100%;margin-top:14px}} th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #eee}}
th{{color:#666;font-size:12px;text-transform:uppercase}}</style></head><body>
<h2>Outreach CRM</h2>
<div class=bar><span>Invited {n_inv}</span><span>🟢 Accepted {n_acc}</span><span>✉️ Messaged {n_msg}</span><span>✅ Replied {n_rep}</span><span>Uninvited left {n_left}</span></div>
<div class=actions>
 <form method=post action=/send style="display:flex;gap:8px;align-items:center;margin:0">
   <button {'disabled' if STATE['busy'] else ''}>Send invites</button>
   <input name=n type=number value=10 min=1 max=50> next uninvited</form>
 <form method=post action=/update style=margin:0><button {'disabled' if STATE['busy'] else ''}>Update CRM</button></form>
</div>
<div class=status>{html.escape(STATE['msg'])}</div>
<b>Next to follow up:</b> {nxt} <span style=color:#888>({TZ.key})</span>
<table><tr><th>Name</th><th>Company</th><th>Status</th><th>Msg</th><th>Follow up</th><th>LI</th></tr>{''.join(trs)}</table>
</body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("content-type","text/html"); self.end_headers()
        self.wfile.write(page().encode())
    def do_POST(self):
        ln=int(self.headers.get("content-length",0)); body=parse_qs(self.rfile.read(ln).decode())
        p=urlparse(self.path).path
        if not STATE["busy"]:
            if p=="/send":   run_bg(do_send, max(1,min(50,int(body.get("n",["10"])[0]))))
            elif p=="/update": run_bg(do_update)
        self.send_response(303); self.send_header("location","/"); self.end_headers()

if __name__=="__main__":
    print(f"CRM ({ACCT}) at http://127.0.0.1:{PORT}  ·  csv={CSV}  ·  tz={TZ.key}")
    ThreadingHTTPServer(("127.0.0.1",PORT), H).serve_forever()
