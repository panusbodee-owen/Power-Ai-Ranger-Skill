#!/usr/bin/env python3
"""แดชบอร์ด: python3 ~/.ai-team/dash.py  แล้วเปิด http://localhost:8790
อ่าน/เขียนเฉพาะ seats/catalog/plan — ไม่เคย select api_key ออกมา
POST ป้องกัน CSRF: ต้องมี Host/Origin เป็น localhost:8790 และ header X-Dash"""
import json, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from team import db, setting, seat_set, seat_undo, seat_add, catalog_test, set_position, valid_base_url, remote_models, import_models, MODES

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = [
    {"id": "tokenme", "title": "Tokenme", "name": "tokenme", "base_url": "https://api.tokenme.limited/api/v1", "filter": ""},
    {"id": "openrouter", "title": "OpenRouter", "name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "filter": ":free"},
    {"id": "nvidia", "title": "NVIDIA NIM", "name": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "filter": "nemotron"},
    {"id": "groq", "title": "Groq", "name": "groq", "base_url": "https://api.groq.com/openai/v1", "filter": ""},
]
PORT = int(os.environ.get("AI_TEAM_PORT", 8790))
OK_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


def state():
    c = db()
    host = lambda u: urlparse(u).netloc
    gws = {n: host(u) for n, u in c.execute("select name,base_url from gateways")}
    catalog = [dict(name=n, gateway=g, host=gws.get(g, g), model=m, cin=ci, cout=co, status=s)
               for n, g, m, ci, co, s in c.execute("select name,gateway,model,cin,cout,status from catalog order by rowid")]
    cat = {k["name"]: k for k in catalog}
    seats = [dict(name=n, title=t, role=r, position=pos, catalog=k, model=cat[k]["model"] if k in cat else None,
                  host=cat[k]["host"] if k in cat else None, cin=cat[k]["cin"] if k in cat else None, cout=cat[k]["cout"] if k in cat else None)
             for n, t, r, k, pos in c.execute("select name,title,role,catalog,position from seats order by rowid")]
    kinds = {n: k for n, k in c.execute("select name,kind from roles")}
    agents = [dict(name=s["name"], title=s["title"], model=s["model"], role=s["role"], host=s["host"], position=s.get("position"), kind=kinds.get(s.get("position") or "maker", "maker")) for s in seats if s["model"]]
    audit = [dict(id=i, t=t, agent=a, model=m, brief=b, reply=r, blocked=bl, job=j, ms=ms) for i, t, a, m, b, r, bl, j, ms in
             c.execute("select rowid,t,agent,model,brief,reply,blocked,job,ms from audit order by rowid desc limit 150")]
    notes = [dict(t=t, job=j, text=x) for t, j, x in c.execute("select t,job,text from notes order by t desc limit 100")]
    plan = dict(n=int(setting(c, "n", 2)), mode=setting(c, "mode", "best-of-n"))
    roles = [dict(name=n, title=t, kind=k) for n, t, k in c.execute("select name,title,kind from roles order by rowid")]
    tasks = [dict(id=i, job=j, status=st, seat=u, trace=json.loads(tr) if tr else None, error=e) for i, j, st, u, tr, e in c.execute("select id,job,status,used_seat,trace,error from tasks where trace is not null order by id desc limit 60")]
    gateways = [dict(name=n, base_url=u, host=host(u), has_key=bool(k), models=sum(1 for x in catalog if x["gateway"] == n))
                for n, u, k in c.execute("select name,base_url,api_key from gateways")]
    queue = dict(c.execute("select status,count(*) from tasks group by status").fetchall())
    return dict(agents=agents, seats=seats, catalog=catalog, plan=plan, queue=queue, gateways=gateways, presets=PRESETS, roles=roles, tasks=tasks, audit=audit, notes=notes,
                can_undo=bool(c.execute("select 1 from seat_log limit 1").fetchone()))


def act(path, b):
    c = db()
    if path == "/api/seat":
        seat_set(c, b["seat"], b.get("catalog"))
    elif path == "/api/position":
        set_position(c, b["seat"], b.get("role") or None)
    elif path == "/api/gateway":
        name, url, key = str(b.get("name", "")).strip(), valid_base_url(b.get("base_url")), str(b.get("api_key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name):
            raise ValueError("ชื่อใช้ได้เฉพาะ a-z 0-9 . _ - ไม่เกิน 40 ตัว")
        old = c.execute("select api_key from gateways where name=?", (name,)).fetchone()
        if not key:
            if not old:
                raise ValueError("ต้องใส่ API key")
            key = old[0]
        if re.search(r"\s", key) or len(key) > 600:
            raise ValueError("API key ไม่ถูกต้อง (มีช่องว่างหรือยาวเกินไป)")
        c.execute("insert or replace into gateways values(?,?,?)", (name, url, key))
        c.commit()
    elif path == "/api/gateway_test":
        return {"ok": True, "models": len(remote_models(c, str(b["name"])))}
    elif path == "/api/import":
        added, matched, total = import_models(c, str(b["name"]), str(b.get("grep") or ""))
        return {"ok": True, "added": added, "matched": matched, "total": total}
    elif path == "/api/gateway_delete":
        name = str(b["name"])
        used = c.execute("select count(*) from seats s join catalog k on k.name=s.catalog where k.gateway=?", (name,)).fetchone()[0]
        if used:
            raise ValueError(f"ยังมี {used} ช่องในทีมใช้โมเดลของเกตเวย์นี้ ล้างช่องก่อน")
        c.execute("delete from catalog where gateway=?", (name,))
        c.execute("delete from gateways where name=?", (name,))
        c.commit()
    elif path == "/api/undo":
        seat_undo(c)
    elif path == "/api/seat_add":
        seat_add(c)
    elif path == "/api/plan":
        n, mode = int(b["n"]), b["mode"]
        if not 1 <= n <= 20 or mode not in MODES:
            raise ValueError("ค่าไม่ถูกต้อง")
        c.execute("insert or replace into settings values('n',?)", (str(n),))
        c.execute("insert or replace into settings values('mode',?)", (mode,))
        c.commit()
    elif path == "/api/test":
        return {"status": catalog_test(c, b["catalog"])}
    else:
        raise ValueError("ไม่รู้จักคำสั่ง")
    return {"ok": True}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ct="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ct + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/usage":
            import usage
            self._send(200, json.dumps(usage.all_usage(db()), ensure_ascii=False).encode())
        elif self.path == "/api/state":
            self._send(200, json.dumps(state(), ensure_ascii=False).encode())
        else:
            self._send(200, open(os.path.join(HERE, "dash.html"), "rb").read(), "text/html")

    def do_POST(self):
        origin = self.headers.get("Origin")
        if (self.headers.get("Host") not in OK_HOSTS or self.headers.get("X-Dash") != "1"
                or (origin and urlparse(origin).netloc not in OK_HOSTS)):
            return self._send(403, b'{"error":"forbidden"}')
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self._send(200, json.dumps(act(self.path, body), ensure_ascii=False).encode())
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
