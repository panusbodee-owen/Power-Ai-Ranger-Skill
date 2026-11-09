#!/usr/bin/env python3
"""แดชบอร์ดอ่านอย่างเดียว: python3 ~/.ai-team/dash.py  แล้วเปิด http://localhost:8790
อ่านเฉพาะคอลัมน์ที่ปลอดภัย — ไม่เคย select api_key"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from team import db

HERE = os.path.dirname(os.path.abspath(__file__))


def state():
    c = db()
    agents = [dict(name=n, title=t, model=m, role=r) for n, t, m, r in
              c.execute("select name,title,model,role from agents")]
    audit = [dict(id=i, t=t, agent=a, model=m, brief=b, reply=r, blocked=bl, job=j, ms=ms) for i, t, a, m, b, r, bl, j, ms in
             c.execute("select rowid,t,agent,model,brief,reply,blocked,job,ms from audit order by rowid desc limit 150")]
    notes = [dict(t=t, job=j, text=x) for t, j, x in c.execute("select t,job,text from notes order by t desc limit 100")]
    return dict(agents=agents, audit=audit, notes=notes)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/state":
            body, ct = json.dumps(state(), ensure_ascii=False).encode(), "application/json"
        else:
            body, ct = open(os.path.join(HERE, "dash.html"), "rb").read(), "text/html"
        self.send_response(200); self.send_header("Content-Type", ct + "; charset=utf-8"); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("http://localhost:8790")
    ThreadingHTTPServer(("127.0.0.1", 8790), H).serve_forever()
