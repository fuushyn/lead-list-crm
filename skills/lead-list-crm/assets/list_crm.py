#!/usr/bin/env python3
"""Generic list-based CRM — zero dependencies, stdlib http.server only.

Drop any contacts CSV → browse, click-to-cycle status, star, take notes.
State lives entirely in the CRM CSV; source CSV is read once to bootstrap.

Env:
  CRM_SRC       path to source CSV (bootstrapped once into CRM_CSV)
  CRM_CSV       working CRM file (default: crm.csv alongside CRM_SRC)
  CRM_PORT      HTTP port (default: 8899)
  CRM_TITLE     page title (default: CRM)
  CRM_STATUSES  JSON array to override default statuses
  CRM_CAT_COL   column name to use as the category/filter pill (optional)

Run: python list_crm.py
"""
import csv, json, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SRC   = os.environ.get("CRM_SRC", "")
CRM   = os.environ.get("CRM_CSV", os.path.join(os.path.dirname(os.path.abspath(SRC or __file__)), "crm.csv"))
PORT  = int(os.environ.get("CRM_PORT", "8899"))
TITLE = os.environ.get("CRM_TITLE", "CRM")
CAT_COL = os.environ.get("CRM_CAT_COL", "")

DEFAULT_STATUSES = [
    "To review", "Following",
    "Sent invite", "Connected",
    "Sent message", "Sent email",
    "Replied", "Talked", "Pass",
]
STATUSES = json.loads(os.environ["CRM_STATUSES"]) if os.environ.get("CRM_STATUSES") else DEFAULT_STATUSES

EXTRA_COLS = ["status", "priority", "notes", "last_touch"]
LOCK = threading.Lock()

# Colour map for status pills. Extend freely.
STATUS_CSS = """
 .st[data-v="To review"]{border-color:var(--line);color:var(--mut);background:#f0f2f5}
 .st[data-v="Following"]{border-color:#2da44e;color:#1a7f37;background:#eaf7ee}
 .st[data-v="Sent invite"]{border-color:#7c3aed;color:#6d28d9;background:#f3effe}
 .st[data-v="Connected"]{border-color:#0ea5e9;color:#0369a1;background:#e0f4fe}
 .st[data-v="Sent message"]{border-color:#bf8700;color:#9a6700;background:#fdf6e3}
 .st[data-v="Sent email"]{border-color:#d97706;color:#b45309;background:#fff7ed}
 .st[data-v="Replied"]{border-color:#2563eb;color:#1d4ed8;background:#eaf0fe}
 .st[data-v="Talked"]{border-color:#059669;color:#065f46;background:#d1fae5}
 .st[data-v="Pass"]{opacity:.35;border-color:var(--line);color:var(--mut);background:transparent}
"""


def detect_src_cols():
    if not SRC or not os.path.exists(SRC):
        return []
    with open(SRC, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f).fieldnames or [])


def bootstrap():
    if os.path.exists(CRM):
        return
    if not SRC or not os.path.exists(SRC):
        save([], detect_src_cols())
        return
    rows = []
    with open(SRC, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r.setdefault("status", "To review")
            r.setdefault("priority", "")
            r.setdefault("notes", "")
            r.setdefault("last_touch", "")
            rows.append(r)
    src_cols = list(rows[0].keys()) if rows else []
    save(rows, src_cols)


def crm_cols():
    if not os.path.exists(CRM):
        return detect_src_cols() + EXTRA_COLS
    with open(CRM, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f).fieldnames or [])


def load():
    with open(CRM, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows, cols=None):
    if cols is None:
        cols = crm_cols()
    # Ensure extra cols are present
    for ec in EXTRA_COLS:
        if ec not in cols:
            cols.append(ec)
    tmp = CRM + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, CRM)


def display_cols(all_cols):
    """Return the non-extra source columns to show as identity in the table."""
    return [c for c in all_cols if c not in EXTRA_COLS]


PAGE_TMPL = r"""<!doctype html><html><head><meta charset=utf-8>
<title>%TITLE%</title>
<style>
 :root{--bg:#f7f8fa;--card:#fff;--line:#e3e6eb;--ink:#1a1d23;--mut:#677084;--acc:#2563eb}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
 header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;z-index:5}
 h1{font-size:17px;margin:0 0 10px} .sub{color:var(--mut);font-size:12px;font-weight:400}
 .bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 input,select{background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:7px 10px;font:inherit}
 input#q{min-width:240px} .stat{margin-left:auto;color:var(--mut);font-size:12px}
 .wrap{padding:8px 20px 60px}
 table{border-collapse:collapse;width:100%} th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
 th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;position:sticky;top:96px;background:var(--bg)}
 tr:hover td{background:#f0f3f7}
 .tag{display:inline-block;background:#eef1f6;border:1px solid var(--line);color:var(--mut);border-radius:999px;padding:2px 8px;font-size:11px;white-space:nowrap}
 a{color:var(--acc);text-decoration:none} a:hover{text-decoration:underline}
 .mut{color:var(--mut);font-size:12px}
 td.notes-col{max-width:340px;color:var(--mut);font-size:12.5px}
 .st{display:inline-block;font-size:11px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);cursor:pointer;user-select:none;white-space:nowrap;transition:opacity .1s}
 .st:hover{opacity:.75}
 %STATUS_CSS%
 .star{cursor:pointer;font-size:16px;color:#ccd1da;user-select:none} .star.on{color:#e3a008}
 textarea{width:100%;background:transparent;border:1px solid transparent;color:var(--mut);font:inherit;resize:vertical;border-radius:6px;padding:4px}
 textarea:focus{background:#f7f8fa;border-color:var(--line);outline:none;color:var(--ink)}
</style></head><body>
<header>
 <h1>%TITLE% <span class=sub id=meta></span></h1>
 <div class=bar>
  <input id=q placeholder="Search…" oninput=render()>
  %CAT_SELECT%
  <select id=st onchange=render()><option value="">All statuses</option></select>
  <label class=mut><input type=checkbox id=fav onchange=render()> ★ only</label>
  <span class=stat id=stat></span>
 </div>
</header>
<div class=wrap><table><thead><tr>
 <th>★</th>%TH_COLS%<th>Status</th><th>Notes</th>
</tr></thead><tbody id=tb></tbody></table></div>
<script>
let DATA=[];
const STATUSES=%STATUSES%;
const DISPLAY_COLS=%DISPLAY_COLS%;
const CAT_COL=%CAT_COL%;
async function boot(){
 DATA=await (await fetch('/data')).json();
 if(CAT_COL){const cats=[...new Set(DATA.map(r=>r[CAT_COL]).filter(Boolean))].sort();
  document.getElementById('cat').innerHTML='<option value="">All '+CAT_COL+'</option>'+cats.map(c=>`<option>${esc(c)}</option>`).join('');}
 document.getElementById('st').innerHTML='<option value="">All statuses</option>'+STATUSES.map(s=>`<option>${esc(s)}</option>`).join('');
 render();}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function render(){
 const q=document.getElementById('q').value.toLowerCase();
 const catEl=document.getElementById('cat'); const c=catEl?catEl.value:'';
 const s=document.getElementById('st').value, fv=document.getElementById('fav').checked;
 const rows=DATA.map((r,i)=>({...r,_i:i})).filter(r=>
   (!c||r[CAT_COL]===c)&&(!s||r.status===s)&&(!fv||r.priority==='1')&&
   (!q||DISPLAY_COLS.concat(['notes']).some(k=>(r[k]||'').toLowerCase().includes(q))));
 document.getElementById('meta').textContent=`· ${DATA.length} total`;
 const starred=DATA.filter(r=>r.priority==='1').length;
 document.getElementById('stat').textContent=`${rows.length} shown · ${starred} ★`;
 tb.innerHTML=rows.map(r=>{
  const cells=DISPLAY_COLS.map(k=>{
   const v=r[k]||'';
   if(k===CAT_COL) return `<td><span class=tag>${esc(v)}</span></td>`;
   if(/^https?:\/\//.test(v)) return `<td><a href="${esc(v)}" target=_blank>link</a></td>`;
   return `<td>${esc(v)}</td>`;
  }).join('');
  return `<tr>
   <td><span class="star ${r.priority==='1'?'on':''}" onclick="star(${r._i},this)">★</span></td>
   ${cells}
   <td><span class=st data-v="${esc(r.status)}" onclick="cycle(${r._i},this)">${esc(r.status)}</span></td>
   <td class=notes-col><textarea rows=2 onchange="upd(${r._i},'notes',this.value)">${esc(r.notes)}</textarea></td>
  </tr>`;}).join('');
}
async function upd(i,f,v){DATA[i][f]=v;await fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({i,f,v})});if(f==='status')render();}
function cycle(i,el){const cur=DATA[i].status;const next=STATUSES[(STATUSES.indexOf(cur)+1)%STATUSES.length];el.textContent=next;el.setAttribute('data-v',next);upd(i,'status',next);}
function star(i,el){const v=DATA[i].priority==='1'?'':'1';DATA[i].priority=v;el.classList.toggle('on',v==='1');upd(i,'priority',v);}
boot();
</script></body></html>"""


def build_page(cols):
    dc = display_cols(cols)
    cat_col = CAT_COL if CAT_COL and CAT_COL in dc else (dc[1] if len(dc) > 1 else "")
    cat_select = (f'<select id=cat onchange=render()></select>' if cat_col else
                  '<span id=cat style=display:none></span>')
    th_cols = "".join(f"<th>{c}</th>" for c in dc)
    return (PAGE_TMPL
            .replace("%TITLE%", TITLE)
            .replace("%STATUS_CSS%", STATUS_CSS)
            .replace("%CAT_SELECT%", cat_select)
            .replace("%TH_COLS%", th_cols)
            .replace("%STATUSES%", json.dumps(STATUSES))
            .replace("%DISPLAY_COLS%", json.dumps(dc))
            .replace("%CAT_COL%", json.dumps(cat_col)))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            with LOCK:
                cols = crm_cols()
            self._send(build_page(cols))
        elif self.path == "/data":
            with LOCK:
                self._send(json.dumps(load()), "application/json")
        else:
            self._send("not found", code=404)

    def do_POST(self):
        if self.path != "/update":
            return self._send("no", code=404)
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or "{}")
        with LOCK:
            cols = crm_cols()
            rows = load()
            i = d.get("i")
            if isinstance(i, int) and 0 <= i < len(rows) and d.get("f") in cols:
                rows[i][d["f"]] = d.get("v", "")
                save(rows, cols)
        self._send(json.dumps({"ok": True}), "application/json")


if __name__ == "__main__":
    bootstrap()
    print(f"{TITLE} CRM → http://127.0.0.1:{PORT}  (state: {os.path.basename(CRM)})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
