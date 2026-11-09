#!/usr/bin/env python3
"""ทีม AI ใต้ Claude (Head): เก็บ API ใน SQLite ให้ Claude สั่งงานผ่านคำสั่งนี้
  team.py add NAME --base-url URL --model MODEL [--title T] [--role R]   (key: ถามแบบซ่อน หรือ env AI_TEAM_KEY)
  team.py list | models NAME [คำค้น] | set NAME [--model X] [--base-url U] | remove NAME | log [N]
  team.py ask NAME "brief" [--job "ชื่องาน"]     (หรือ brief เป็น -  เพื่ออ่านจาก stdin)
  team.py note "ชื่องาน" "ผลตรวจของ Claude"       (แสดงบนแดชบอร์ด)
DB: ~/.ai-team/team.db (env AI_TEAM_DB) — ไม่มีคำสั่งใดพิมพ์ key ออกมา
"""
import argparse, getpass, json, os, re, sqlite3, sys, time, urllib.error, urllib.request

DB = os.environ.get("AI_TEAM_DB", os.path.expanduser("~/.ai-team/team.db"))
BLOCK = [r"sk-[A-Za-z0-9]{20,}", r"(?i)api[_-]?key\s*[:=]", r"\b\d{13}\b", r"(?i)password\s*[:=]"]
MAX_CHARS, MAX_CALLS_PER_HOUR = 8000, 30
UA = {"User-Agent": "ai-team/1.0"}


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""create table if not exists agents(name text primary key,title text,base_url text,
        model text,api_key text,role text);
        create table if not exists audit(t real,agent text,model text,brief text,reply text,blocked text);
        create table if not exists notes(t real,job text,text text);""")
    if "job" not in [r[1] for r in c.execute("pragma table_info(audit)")]:
        c.execute("alter table audit add column job text")
    if "ms" not in [r[1] for r in c.execute("pragma table_info(audit)")]:
        c.execute("alter table audit add column ms integer")
    os.chmod(DB, 0o600)
    return c


def ask(c, name, brief, job=None):
    row = c.execute("select base_url,model,api_key,role from agents where name=?", (name,)).fetchone()
    if not row:
        sys.exit(f"ไม่พบ agent '{name}' (ดู: team.py list)")
    url, model, key, role = row
    why = None
    if len(brief) > MAX_CHARS:
        why = "brief ยาวเกินกำหนด"
    elif any(re.search(p, brief) for p in BLOCK):
        why = "brief มีข้อมูลต้องห้าม (key/รหัสผ่าน/เลข 13 หลัก)"
    elif c.execute("select count(*) from audit where t>? and blocked is null", (time.time() - 3600,)).fetchone()[0] >= MAX_CALLS_PER_HOUR:
        why = "เกินงบจำนวนครั้งต่อชั่วโมง"
    if why:
        c.execute("insert into audit(t,agent,model,brief,blocked,job) values(?,?,?,?,?,?)",
                  (time.time(), name, model, brief, why, job)); c.commit()
        sys.exit(f"[BLOCKED] {why}")
    rid = c.execute("insert into audit(t,agent,model,brief,job) values(?,?,?,?,?)",
                    (time.time(), name, model, brief, job)).lastrowid
    c.commit()  # แถวที่ยังไม่มี reply = "กำลังทำงาน" บนแดชบอร์ด

    def fail(msg):
        c.execute("update audit set blocked=? where rowid=?", ("error: " + msg, rid)); c.commit()
        sys.exit(f"[ERROR] {name}: {msg}")

    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": role or ""}, {"role": "user", "content": brief}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", body,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            reply = json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
    except Exception as e:
        fail(type(e).__name__)
    c.execute("update audit set reply=?, ms=? where rowid=?", (reply, int((time.time() - t0) * 1000), rid)); c.commit()
    return reply


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("add"); a.add_argument("name"); a.add_argument("--base-url", required=True)
    a.add_argument("--model", required=True); a.add_argument("--title", default=""); a.add_argument("--role", default="")
    sp.add_parser("list"); l = sp.add_parser("log"); l.add_argument("n", nargs="?", type=int, default=10)
    r = sp.add_parser("remove"); r.add_argument("name")
    mo = sp.add_parser("models"); mo.add_argument("name"); mo.add_argument("grep", nargs="?", default="")
    st = sp.add_parser("set"); st.add_argument("name"); st.add_argument("--model"); st.add_argument("--base-url")
    k = sp.add_parser("ask"); k.add_argument("name"); k.add_argument("brief"); k.add_argument("--job")
    nt = sp.add_parser("note"); nt.add_argument("job"); nt.add_argument("text")
    x = ap.parse_args(); c = db()
    if x.cmd == "add":
        key = os.environ.get("AI_TEAM_KEY") or getpass.getpass(f"API key ของ {x.name} (พิมพ์แล้วไม่แสดง): ")
        c.execute("insert or replace into agents values(?,?,?,?,?,?)",
                  (x.name, x.title or x.name, x.base_url, x.model, key, x.role)); c.commit()
        print(f"บันทึก {x.name} ({x.model}) แล้ว")
    elif x.cmd == "list":
        for n, t, u, m, ro in c.execute("select name,title,base_url,model,role from agents"):
            print(f"{n}\t{t}\t{m}\t{u}\t{ro[:50]}")
    elif x.cmd == "log":
        for t, n, m, b, bl, j in c.execute("select t,agent,model,brief,blocked,job from audit order by t desc limit ?", (x.n,)):
            print(time.strftime("%H:%M:%S", time.localtime(t)), n, m, bl or "ok", f"[{j}]" if j else "", "|", b[:60].replace("\n", " "))
    elif x.cmd == "models":
        url, key = c.execute("select base_url,api_key from agents where name=?", (x.name,)).fetchone()
        rq = urllib.request.Request(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}", **UA})
        try:
            ids = sorted(m["id"] for m in json.load(urllib.request.urlopen(rq, timeout=60))["data"])
        except Exception as e:
            sys.exit(f"[ERROR] {type(e).__name__}")
        print("\n".join(i for i in ids if x.grep.lower() in i.lower()))
    elif x.cmd == "set":
        for col, v in (("model", x.model), ("base_url", x.base_url)):
            if v: c.execute(f"update agents set {col}=? where name=?", (v, x.name))
        c.commit()
    elif x.cmd == "remove":
        c.execute("delete from agents where name=?", (x.name,)); c.commit()
    elif x.cmd == "note":
        c.execute("insert into notes values(?,?,?)", (time.time(), x.job, x.text)); c.commit()
    else:
        print(ask(c, x.name, sys.stdin.read() if x.brief == "-" else x.brief, x.job))


if __name__ == "__main__":
    main()
