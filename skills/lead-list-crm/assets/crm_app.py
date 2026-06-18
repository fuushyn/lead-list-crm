#!/usr/bin/env python3
"""Barebones outreach CRM — a local web app with buttons over CSV "lists".

  Send invites N  -> LinkedIn connection requests to the next N UNINVITED rows (paced).
  Update CRM      -> reconcile from LinkedIn Sent box, then re-check invited rows
                     (accepts / messaged / replied) + recompute next_followup.
  Update all      -> same, for EVERY contact, not just invited.

Multiple lists = multiple `crm*.csv` files in the working dir, shown as tabs
(crm.csv -> "main", crm_procore.csv -> "procore", ...). Each list has INDEPENDENT
state (its own busy flag, status message, and lock) — actions never cross-pollute.

Env: UNIPILE_DSN, UNIPILE_API_KEY (req); UNIPILE_ACCOUNT_ID, CRM_PORT (8787), CRM_TZ.
CSV cols: person_name,company,role,member_id,linkedin,invited,invited_date,
          invite_status,messaged,replied,last_touch,next_followup,notes
Run: python crm_app.py
"""
import os, csv, json, time, random, threading, glob, urllib.request, urllib.error, html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote

DSN  = os.environ["UNIPILE_DSN"].rstrip("/")
KEY  = os.environ["UNIPILE_API_KEY"]
PORT = int(os.environ.get("CRM_PORT", "8787"))
TZ   = ZoneInfo(os.environ.get("CRM_TZ", "America/Los_Angeles"))
COLS = ["person_name","company","role","member_id","linkedin","invited","invited_date",
        "invite_status","messaged","replied","last_touch","next_followup","notes"]
MAX_ROWS = 2000

# per-list state + locks (no cross-tab pollution)
STATE = {}; LOCKS = {}
def st(label): return STATE.setdefault(label, {"busy": False, "msg": "Ready."})
def lk(label): return LOCKS.setdefault(label, threading.Lock())

def today():  return datetime.now(TZ).date()
def ds(d):    return d.strftime("%Y-%m-%d")
def hfmt(s):
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

# ---------- lists (tabs) ----------
def lists():
    out = {}
    for f in sorted(glob.glob("crm*.csv")):
        base = f[:-4]
        label = "main" if base == "crm" else base.replace("crm_", "").replace("crm-", "")
        out[label] = f
    return out or {"main": "crm.csv"}

def resolve(label):
    L = lists()
    if label in L: return label, L[label]
    first = next(iter(L)); return first, L[first]

def load(path):
    with open(path, newline="") as f: return list(csv.DictReader(f))
def save(path, rows):
    with open(path+".tmp","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=COLS); w.writeheader()
        for r in rows: w.writerow({k:r.get(k,"") for k in COLS})
    os.replace(path+".tmp", path)
def nrows(path):
    try:
        with open(path, newline="") as f: return sum(1 for _ in csv.reader(f)) - 1
    except Exception: return 0

# ---------- SEND INVITES ----------
ACCOUNT_ERRS = ("disconnected_account", "missing_credentials", "invalid_credentials", "credentials")
def account_blocked():
    s, d = api("GET", f"/api/v1/accounts/{ACCT}")
    if s != 200: return f"account check failed (HTTP {s})"
    sts = [x.get("status") for x in d.get("sources", [])]
    if not sts or any(x != "OK" for x in sts): return f"not connected (source status: {','.join(sts) or 'unknown'})"
    return None

def do_send(label, n):
    S = st(label); _, path = resolve(label); rows = load(path)
    reason = account_blocked()
    if reason:
        S["msg"] = f"❌ ABORTED — LinkedIn account {reason}. Reconnect at dashboard.unipile.com, then retry. No invites sent."
        return
    queue = [r for r in rows if r.get("invited") != "yes" and r.get("member_id")][:n]
    S["msg"] = f"Sending {len(queue)} invites (paced)…"; sent = fail = 0
    for i, r in enumerate(queue, 1):
        sc, resp = api("POST", "/api/v1/users/invite", {"provider_id": r["member_id"], "account_id": ACCT})
        etype = (resp or {}).get("type", "")
        if sc == 401 or any(e in etype for e in ACCOUNT_ERRS):
            S["msg"] = (f"❌ ABORTED at {i}/{len(queue)} — LinkedIn disconnected ({etype or 'HTTP '+str(sc)}). "
                        f"{sent} sent before failure; remaining untouched. Reconnect in Unipile, then retry.")
            return
        if sc in (200, 201):
            r["invited"]="yes"; r["invited_date"]=ds(today()); r["last_touch"]=ds(today()); r["invite_status"]="pending"; sent += 1
        elif "already_invited" in etype:
            r["invited"]="yes"; r["invited_date"]=ds(today()); r["invite_status"]="already_invited"; sent += 1
        else:
            fail += 1; r["notes"]=(r.get("notes","")+f" invite_err:{etype or sc}").strip()
        save(path, rows); S["msg"]=f"Sending… {i}/{len(queue)} ({sent} ok, {fail} fail)"
        if i < len(queue): time.sleep(random.uniform(150,240) if i % 10 == 0 else random.uniform(35,75))
    S["msg"]=f"Done: sent {sent}, failed {fail}. Run Update CRM in a day or two to track accepts."

# ---------- UPDATE ----------
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
def reconcile_sent(rows):
    sent = {}; cur = None
    for _ in range(20):
        _, d = api("GET", f"/api/v1/users/invite/sent?account_id={ACCT}&limit=100"+(f"&cursor={cur}" if cur else ""))
        for it in d.get("items", []):
            mid = it.get("invited_user_id")
            if mid: sent.setdefault(mid, (it.get("parsed_datetime") or "")[:10])
        cur = d.get("cursor")
        if not cur: break
    n = 0
    for r in rows:
        if r.get("member_id") in sent and r.get("invited") != "yes":
            r["invited"]="yes"; r["invite_status"]=r.get("invite_status") or "pending"
            r["invited_date"]=r.get("invited_date") or sent[r["member_id"]] or ds(today())
            r["last_touch"]=r.get("last_touch") or r["invited_date"]; n += 1
    return n

def do_update(label, scope):
    S = st(label); _, path = resolve(label); rows = load(path)
    S["msg"]="Reconciling sent invites…"; recon = reconcile_sent(rows)
    if recon: save(path, rows)
    S["msg"]="Fetching chats…"; chats = chat_index()
    inv = [r for r in rows if r.get("member_id")] if scope=="all" \
          else [r for r in rows if r.get("invited")=="yes" and r.get("member_id")]
    for i, r in enumerate(inv, 1):
        S["msg"]=f"Updating ({scope})… {i}/{len(inv)}"
        _, d = api("GET", f"/api/v1/users/{r['member_id']}?account_id={ACCT}")
        nd = d.get("network_distance","")
        r["invite_status"]="accepted" if nd=="FIRST_DEGREE" else (r.get("invite_status") or "pending")
        cid = chats.get(r["member_id"])
        if cid: r["messaged"]="yes"; r["replied"]="yes" if has_reply(cid) else "no"
        time.sleep(0.5)
        if r["replied"]=="yes":
            r["next_followup"]=ds(today()); r["notes"]=("HOT: replied — respond now. "+r.get("notes","")).strip()
        elif r["invite_status"]=="accepted" and r["messaged"]!="yes": r["next_followup"]=ds(today())
        elif r["invite_status"]=="accepted" and r["messaged"]=="yes": r["next_followup"]=ds(today()+timedelta(days=3))
        else: r["next_followup"]=""
        save(path, rows)
    acc=sum(1 for r in inv if r["invite_status"]=="accepted"); rep=sum(1 for r in inv if r.get("replied")=="yes")
    due=sorted([r for r in rows if r.get("next_followup")], key=lambda r:r["next_followup"])
    nxt = hfmt(due[0]["next_followup"]) if due else "—"
    rec = f" +{recon} reconciled from Sent." if recon else ""
    S["msg"]=f"Updated {len(inv)} ({scope}).{rec} Accepted: {acc} · Replied: {rep}. Next follow-up: {nxt}."

def run_bg(label, fn, *a):
    S = st(label)
    def wrap():
        with lk(label):
            S["busy"]=True
            try: fn(label, *a)
            except Exception as e: S["msg"]=f"Error: {e}"
            finally: S["busy"]=False
    threading.Thread(target=wrap, daemon=True).start()

# ---------- UI ----------
PAGE_SIZE = 50
FILTERS = ["all","uninvited","pending","accepted","messaged","replied"]
def matches(r, status):
    if status=="uninvited": return r.get("invited")!="yes"
    if status=="pending":   return r.get("invited")=="yes" and r.get("invite_status")!="accepted" and r.get("replied")!="yes"
    if status=="accepted":  return r.get("invite_status")=="accepted"
    if status=="messaged":  return r.get("messaged")=="yes"
    if status=="replied":   return r.get("replied")=="yes"
    return True

def page(active, status="all", q="", pg=1):
    active, path = resolve(active); S = st(active); busy = S["busy"]
    rows = load(path)
    c=lambda f,v: sum(1 for r in rows if r.get(f)==v)
    n_inv=c("invited","yes"); n_acc=c("invite_status","accepted"); n_msg=c("messaged","yes"); n_rep=c("replied","yes")
    n_left=sum(1 for r in rows if r.get("invited")!="yes" and r.get("member_id"))
    acc_rate = f"{round(100*n_acc/n_inv)}%" if n_inv else "—"
    rep_rate = f"{round(100*n_rep/n_msg)}%" if n_msg else "—"
    due=sorted([r for r in rows if r.get("next_followup")], key=lambda r:r["next_followup"])
    refresh='<meta http-equiv="refresh" content="4">' if busy else ''
    show=sorted(rows, key=lambda r:(0 if r.get("replied")=="yes" else 1,
                                    0 if r.get("invite_status")=="accepted" else 1,
                                    0 if r.get("invited")=="yes" else 2, r.get("company","")))
    if status not in FILTERS: status="all"
    ql=q.lower().strip()
    fl=[r for r in show if matches(r,status) and (not ql or ql in (r.get('person_name','')+' '+r.get('company','')+' '+r.get('role','')).lower())]
    total=len(fl); pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE); pg=max(1,min(pg,pages))
    page_rows=fl[(pg-1)*PAGE_SIZE:pg*PAGE_SIZE]
    trs=[]
    for r in page_rows:
        badge="✅ replied" if r.get("replied")=="yes" else ("🟢 accepted" if r.get("invite_status")=="accepted" else ("🟡 pending" if r.get("invited")=="yes" else "—"))
        msg="✉️" if r.get("messaged")=="yes" else ""
        li=f'<a href="{html.escape(r.get("linkedin",""))}" target=_blank>link</a>' if r.get("linkedin") else ""
        trs.append(f"<tr><td>{html.escape(r.get('person_name',''))}</td><td>{html.escape(r.get('company',''))}</td><td>{html.escape(r.get('role',''))}</td><td>{badge}</td><td>{msg}</td><td>{html.escape(hfmt(r.get('next_followup','')))}</td><td>{li}</td></tr>")
    nxt=(html.escape(hfmt(due[0]['next_followup']))+' — '+html.escape(due[0]['person_name'])+' ('+html.escape(due[0]['company'])+')') if due else '— run Update CRM'
    tabs="".join(f'<a class="tab {"on" if lbl==active else ""}" href="/?list={lbl}">{html.escape(lbl)} <small>{nrows(p)}</small>{" ⏳" if st(lbl)["busy"] else ""}</a>' for lbl,p in lists().items())
    h=html.escape
    qenc=quote(q)
    qs=lambda **kw: "&".join(f"{k}={v}" for k,v in {"list":active,"status":status,**({"q":qenc} if q else {}),**kw}.items())
    chips="".join(f'<a class="chip {"on" if s==status else ""}" href="/?{qs(status=s,page=1)}">{s}</a>' for s in FILTERS)
    search=(f'<form method=get style="margin:0;display:flex;gap:6px">'
            f'<input type=hidden name=list value="{h(active)}"><input type=hidden name=status value="{h(status)}">'
            f'<input name=q value="{h(q)}" placeholder="search name / company / segment" style="padding:7px;border:1px solid #ccc;border-radius:6px;width:240px">'
            f'<button>search</button>{f" <a class=clr href=/?list={active}>clear</a>" if q else ""}</form>')
    prev=f'<a href="/?{qs(page=pg-1)}">‹ prev</a>' if pg>1 else '<span class=off>‹ prev</span>'
    nxtl=f'<a href="/?{qs(page=pg+1)}">next ›</a>' if pg<pages else '<span class=off>next ›</span>'
    pager=f'<div class=pg>{prev} &nbsp; page {pg} of {pages} <small>({total} rows{(" · filtered" if (status!="all" or q) else "")})</small> &nbsp; {nxtl}</div>'
    return f"""<!doctype html><html><head><meta charset=utf-8>{refresh}<title>Outreach CRM</title><style>
body{{font:14px -apple-system,system-ui,sans-serif;max-width:1040px;margin:28px auto;padding:0 16px;color:#111}}
.tabs{{display:flex;gap:6px;margin:6px 0 16px;border-bottom:2px solid #eee}}
.tab{{padding:8px 14px;text-decoration:none;color:#666;border-radius:8px 8px 0 0;font-weight:600}}
.tab.on{{background:#111;color:#fff}} .tab small{{opacity:.55;font-weight:500}}
.bar{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;font-weight:600}}
.bar span{{background:#f3f4f6;padding:6px 12px;border-radius:8px}}
.bar .rate{{background:#111;color:#fff;font-size:16px}} .bar .rate small{{opacity:.6;font-weight:500}}
.actions{{display:flex;gap:12px;align-items:center;margin:18px 0;padding:14px;background:#fafafa;border:1px solid #eee;border-radius:10px}}
button{{background:#111;color:#fff;border:0;padding:9px 16px;border-radius:8px;font-weight:600;cursor:pointer}}
button:disabled{{opacity:.4}} input[type=number]{{width:64px;padding:8px;border:1px solid #ccc;border-radius:6px}}
.status{{padding:8px 12px;border-radius:8px;background:{'#fff7ed' if busy else '#ecfdf5'};border:1px solid #eee;margin:10px 0}}
table{{border-collapse:collapse;width:100%;margin-top:14px}} th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #eee}}
th{{color:#666;font-size:12px;text-transform:uppercase}}
.filterbar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}}
.chip{{padding:5px 11px;text-decoration:none;color:#555;background:#f3f4f6;border-radius:999px;font-weight:600;font-size:12px}}
.chip.on{{background:#111;color:#fff}} .clr{{color:#888;text-decoration:none;align-self:center}}
.pg{{margin:10px 0;color:#444}} .pg a{{text-decoration:none;font-weight:600;color:#111}} .pg .off{{color:#bbb}}
</style></head><body>
<h2>Outreach CRM</h2>
<div class=tabs>{tabs}</div>
<div class=bar><span class=rate>Acceptance {acc_rate} <small>({n_acc}/{n_inv})</small></span><span class=rate>Reply {rep_rate} <small>({n_rep}/{n_msg})</small></span></div>
<div class=bar><span>Invited {n_inv}</span><span>🟢 Accepted {n_acc}</span><span>✉️ Messaged {n_msg}</span><span>✅ Replied {n_rep}</span><span>Uninvited left {n_left}</span></div>
<div class=actions>
 <form method=post action=/send style="display:flex;gap:8px;align-items:center;margin:0"><input type=hidden name=list value="{h(active)}">
   <button {'disabled' if busy else ''}>Send invites</button><input name=n type=number value=10 min=1 max=50> next uninvited</form>
 <form method=post action=/update style=margin:0><input type=hidden name=list value="{h(active)}"><button {'disabled' if busy else ''}>Update CRM</button></form>
 <form method=post action=/update_all style=margin:0><input type=hidden name=list value="{h(active)}"><button {'disabled' if busy else ''} title="re-check every contact, not just invited">Update all</button></form>
 <span style="color:#888">list: <b>{h(active)}</b></span>
</div>
<div class=status>{h(S['msg'])}</div>
<b>Next to follow up:</b> {nxt} <span style=color:#888>({TZ.key})</span>
<div class=filterbar>{chips}<span style=flex:1></span>{search}</div>
{pager}
<table><tr><th>Name</th><th>Company</th><th>Segment</th><th>Status</th><th>Msg</th><th>Follow up</th><th>LI</th></tr>{''.join(trs)}</table>
{pager}
</body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        q=parse_qs(urlparse(self.path).query)
        active=q.get("list",["main"])[0]; status=q.get("status",["all"])[0]; qq=q.get("q",[""])[0]
        try: pg=int(q.get("page",["1"])[0])
        except Exception: pg=1
        self.send_response(200); self.send_header("content-type","text/html"); self.end_headers()
        self.wfile.write(page(active, status, qq, pg).encode())
    def do_POST(self):
        ln=int(self.headers.get("content-length",0)); body=parse_qs(self.rfile.read(ln).decode())
        active,_=resolve(body.get("list",["main"])[0]); p=urlparse(self.path).path
        if not st(active)["busy"]:
            if p=="/send":   run_bg(active, do_send, max(1,min(50,int(body.get("n",["10"])[0]))))
            elif p=="/update": run_bg(active, do_update, "invited")
            elif p=="/update_all": run_bg(active, do_update, "all")
        self.send_response(303); self.send_header("location",f"/?list={active}"); self.end_headers()

if __name__=="__main__":
    print(f"CRM ({ACCT}) at http://127.0.0.1:{PORT}  ·  lists={list(lists())}  ·  tz={TZ.key}")
    ThreadingHTTPServer(("127.0.0.1",PORT), H).serve_forever()
