#!/usr/bin/env python3
"""แดชบอร์ด: python3 ~/.ai-team/dash.py  แล้วเปิด http://localhost:8790
อ่าน/เขียนเฉพาะ seats/catalog/plan — ไม่เคย select api_key ออกมา
POST ป้องกัน CSRF: ต้องมี Host/Origin เป็น localhost:8790 และ header X-Dash"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from team import db, setting, seat_set, seat_undo, seat_add, catalog_test, MODES

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8790
OK_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


def state():
    c = db()
    host = lambda u: urlparse(u).netloc
    gws = {n: host(u) for n, u in c.execute("select name,base_url from gateways")}
    catalog = [dict(name=n, gateway=g, host=gws.get(g, g), model=m, cin=ci, cout=co, status=s)
               for n, g, m, ci, co, s in c.execute("select name,gateway,model,cin,cout,status from catalog order by rowid")]
    cat = {k["name"]: k for k in catalog}
    seats = [dict(name=n, title=t, role=r, catalog=k, model=cat[k]["model"] if k in cat else None,
                  host=cat[k]["host"] if k in cat else None, cin=cat[k]["cin"] if k in cat else None, cout=cat[k]["cout"] if k in cat else None)
             for n, t, r, k in c.execute("select name,title,role,catalog from seats order by rowid")]
    agents = [dict(name=s["name"], title=s["title"], model=s["model"], role=s["role"], host=s["host"]) for s in seats if s["model"]]
    audit = [dict(id=i, t=t, agent=a, model=m, brief=b, reply=r, blocked=bl, job=j, ms=ms) for i, t, a, m, b, r, bl, j, ms in
             c.execute("select rowid,t,agent,model,brief,reply,blocked,job,ms from audit order by rowid desc limit 150")]
    notes = [dict(t=t, job=j, text=x) for t, j, x in c.execute("select t,job,text from notes order by t desc limit 100")]
    plan = dict(n=int(setting(c, "n", 2)), mode=setting(c, "mode", "best-of-n"))
    return dict(agents=agents, seats=seats, catalog=catalog, plan=plan, audit=audit, notes=notes,
                can_undo=bool(c.execute("select 1 from seat_log limit 1").fetchone()))


def act(path, b):
    c = db()
    if path == "/api/seat":
        seat_set(c, b["seat"], b.get("catalog"))
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
        if self.path == "/api/state":
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
