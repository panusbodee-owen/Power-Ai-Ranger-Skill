#!/usr/bin/env python3
"""ทีม AI ใต้ Claude (Head): gateways -> catalog (โมเดล) -> seats (ช่องทีม)
  add SEAT --base-url URL --model M [--title T] [--role R]   ทางลัด: สร้าง gateway+catalog+seat (key ถามแบบซ่อน/env AI_TEAM_KEY)
  gateway add|ls | catalog add|ls|import|test|price|remove | seat ls|add|set|clear|remove|undo
  plan [set --n N --mode best-of-n|vote|split]      ค่าที่ผู้ใช้เลือกในแดชบอร์ด
  ask SEAT "brief" [--job J]                         สั่งช่องเดียว
  run --seats a,b [--mode M] [--assign seat=brief ...] [--budget USD] [--job J] ["brief"]
  note "งาน" "ผลตรวจ" | log [N] | models SEAT [คำค้น] | list
DB: ~/.ai-team/team.db (env AI_TEAM_DB) — ไม่มีคำสั่งใดพิมพ์ key ออกมา
"""
import argparse, collections, concurrent.futures as cf, getpass, json, os, re, sqlite3, sys, time, urllib.error, urllib.request
from urllib.parse import urlparse

DB = os.environ.get("AI_TEAM_DB", os.path.expanduser("~/.ai-team/team.db"))
BLOCK = [r"sk-[A-Za-z0-9]{20,}", r"(?i)api[_-]?key\s*[:=]", r"\b\d{13}\b", r"(?i)password\s*[:=]"]
MAX_CHARS, MAX_CALLS_PER_HOUR = 8000, 60
UA = {"User-Agent": "ai-team/1.0"}
PLACEHOLDER = re.compile(r"(?i)xxx\.|your-|url-|example\.|<.*>")
MODES = ("best-of-n", "vote", "split")
SKIP = ("embed", "safety", "guard", "parse", "reward", "rerank", "vl", "content")
SCHEMA = """
create table if not exists agents(name text primary key,title text,base_url text,model text,api_key text,role text);
create table if not exists audit(t real,agent text,model text,brief text,reply text,blocked text);
create table if not exists notes(t real,job text,text text);
create table if not exists gateways(name text primary key,base_url text,api_key text);
create table if not exists catalog(name text primary key,gateway text,model text,cin real,cout real,status text);
create table if not exists seats(name text primary key,title text,role text,catalog text);
create table if not exists seat_log(t real,seat text,old text,new text);
create table if not exists settings(k text primary key,v text);
"""


def cols(c, t):
    return [r[1] for r in c.execute(f"pragma table_info({t})")]


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.executescript(SCHEMA)
    for col, typ in (("job", "text"), ("ms", "integer")):
        if col not in cols(c, "audit"):
            c.execute(f"alter table audit add column {col} {typ}")
    migrate(c)
    os.chmod(DB, 0o600)
    return c


def migrate(c):
    if c.execute("select 1 from settings where k='migrated'").fetchone():
        return
    for name, title, url, model, key, role in c.execute("select name,title,base_url,model,api_key,role from agents").fetchall():
        host = urlparse(url).netloc
        c.execute("insert or ignore into gateways values(?,?,?)", (host, url, key))
        c.execute("insert or ignore into catalog(name,gateway,model) values(?,?,?)", (model.split("/")[-1], host, model))
        c.execute("insert or ignore into seats values(?,?,?,?)", (name, title, role, model.split("/")[-1]))
    for i in (3, 4):
        c.execute("insert or ignore into seats values(?,?,?,null)", (f"seat{i}", f"ช่อง {i}", "ผู้ช่วย AI: ทำงานตามที่ได้รับมอบหมาย กระชับ ถูกต้อง"))
    c.executemany("insert or ignore into settings values(?,?)", [("migrated", "1"), ("n", "2"), ("mode", "best-of-n")])
    c.commit()


def setting(c, k, d=None):
    r = c.execute("select v from settings where k=?", (k,)).fetchone()
    return r[0] if r else d


def chat(url, key, model, role, brief, timeout=180):
    body = json.dumps({"model": model, "messages": [{"role": "system", "content": role or ""}, {"role": "user", "content": brief}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.read()[:160].decode(errors='replace')}")
    except Exception as e:
        raise RuntimeError(type(e).__name__)


def resolve(c, seat):
    return c.execute("""select s.role,k.model,g.base_url,g.api_key,k.name,k.cin,k.cout from seats s
        join catalog k on k.name=s.catalog join gateways g on g.name=k.gateway where s.name=?""", (seat,)).fetchone()


def call(c, seat, brief, job=None):
    """คืน (ok, text) ไม่ throw/exit — ใช้ได้ทั้ง ask และ run"""
    r = resolve(c, seat)
    if not r:
        return False, f"[ERROR] ช่อง '{seat}' ไม่มีหรือยังว่าง (ดู: team.py seat ls)"
    role, model, url, key, _, _, _ = r
    why = None
    if PLACEHOLDER.search(url):
        why = "base_url ยังเป็นค่าตัวอย่าง ห้ามส่ง key ไป"
    elif len(brief) > MAX_CHARS:
        why = "brief ยาวเกินกำหนด"
    elif any(re.search(p, brief) for p in BLOCK):
        why = "brief มีข้อมูลต้องห้าม (key/รหัสผ่าน/เลข 13 หลัก)"
    elif c.execute("select count(*) from audit where t>? and blocked is null", (time.time() - 3600,)).fetchone()[0] >= MAX_CALLS_PER_HOUR:
        why = "เกินงบจำนวนครั้งต่อชั่วโมง"
    if why:
        c.execute("insert into audit(t,agent,model,brief,blocked,job) values(?,?,?,?,?,?)", (time.time(), seat, model, brief, why, job)); c.commit()
        return False, f"[BLOCKED] {why}"
    rid = c.execute("insert into audit(t,agent,model,brief,job) values(?,?,?,?,?)", (time.time(), seat, model, brief, job)).lastrowid
    c.commit()
    t0 = time.time()
    try:
        reply = chat(url, key, model, role, brief)
    except Exception as e:
        c.execute("update audit set blocked=? where rowid=?", (f"error: {e}", rid)); c.commit()
        return False, f"[ERROR] {seat}: {e}"
    c.execute("update audit set reply=?, ms=? where rowid=?", (reply, int((time.time() - t0) * 1000), rid)); c.commit()
    return True, reply


# ---------- seat / catalog operations (ใช้ร่วมกับแดชบอร์ด) ----------
def seat_set(c, seat, cat):
    if cat and not c.execute("select 1 from catalog where name=?", (cat,)).fetchone():
        raise ValueError(f"ไม่พบโมเดล '{cat}' ในแคตตาล็อก")
    old = c.execute("select catalog from seats where name=?", (seat,)).fetchone()
    if old is None:
        raise ValueError(f"ไม่พบช่อง '{seat}'")
    c.execute("insert into seat_log values(?,?,?,?)", (time.time(), seat, old[0], cat))
    c.execute("update seats set catalog=? where name=?", (cat, seat)); c.commit()


def seat_undo(c):
    r = c.execute("select rowid,seat,old from seat_log order by rowid desc limit 1").fetchone()
    if not r:
        return None
    c.execute("update seats set catalog=? where name=?", (r[2], r[1])); c.execute("delete from seat_log where rowid=?", (r[0],)); c.commit()
    return r[1]


def seat_add(c):
    n = 1
    while c.execute("select 1 from seats where name=?", (f"seat{n}",)).fetchone():
        n += 1
    c.execute("insert into seats values(?,?,?,null)", (f"seat{n}", f"ช่อง {n}", "ผู้ช่วย AI: ทำงานตามที่ได้รับมอบหมาย กระชับ ถูกต้อง")); c.commit()
    return f"seat{n}"


def catalog_test(c, name):
    r = c.execute("select k.model,g.base_url,g.api_key from catalog k join gateways g on g.name=k.gateway where k.name=?", (name,)).fetchone()
    if not r or PLACEHOLDER.search(r[1]):
        raise ValueError("ไม่พบโมเดล หรือ base_url ยังเป็นค่าตัวอย่าง")
    try:
        chat(r[1], r[2], r[0], "", "ตอบว่า OK", timeout=60); ok = "ok"
    except Exception:
        ok = "fail"
    c.execute("update catalog set status=? where name=?", (ok, name)); c.commit()
    return ok


def estimate(c, seats, briefs):
    tot, unk = 0.0, False
    for s in seats:
        r = resolve(c, s)
        if not r or r[5] is None or r[6] is None:
            unk = True; continue
        tot += r[5] * (len(briefs[s]) / 3) / 1e6 + r[6] * 1000 / 1e6
    return tot, unk


def run(c, a):
    seats = [s for s in a.seats.split(",") if s]
    assign = dict(x.split("=", 1) for x in (a.assign or []))
    if a.mode == "split":
        seats = [s for s in seats if s in assign]
        briefs = assign
    else:
        if not a.brief:
            sys.exit("ต้องใส่ brief")
        briefs = {s: a.brief for s in seats}
    if not seats:
        sys.exit("ไม่มีช่องที่จะรัน")
    est, unk = estimate(c, seats, briefs)
    print(f"[ประเมิน] {len(seats)} AI · ≈ ${est:.4f}" + (" (บางโมเดลยังไม่ตั้งราคา)" if unk else "") + " · สมมติตอบ ~1000 token")
    if a.budget is not None and est > a.budget:
        sys.exit(f"[BLOCKED] ประเมิน ${est:.4f} เกินงบ ${a.budget}")

    def one(s):
        return s, call(db(), s, briefs[s], a.job)
    with cf.ThreadPoolExecutor(max_workers=max(1, len(seats))) as ex:
        res = list(ex.map(one, seats))
    for s, (ok, text) in res:
        r = resolve(c, s)
        print(f"\n=== {s} ({r[1] if r else '?'}) {'ok' if ok else 'FAIL'}\n{text}")
    if a.mode == "vote":
        norm = lambda t: re.sub(r"\W+", " ", t.strip().splitlines()[0].lower()).strip() if t.strip() else ""
        tally = collections.Counter(norm(t) for s, (ok, t) in res if ok)
        if tally:
            top = tally.most_common()
            tie = len(top) > 1 and top[0][1] == top[1][1]
            print(f"\n[VOTE] {'เสมอ — Claude ตัดสิน' if tie else 'เสียงข้างมาก'}: " + ", ".join(f"'{k}'×{v}" for k, v in top))


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("name"); p.add_argument("--base-url", required=True); p.add_argument("--model", required=True)
    p.add_argument("--title", default=""); p.add_argument("--role", default="")
    sp.add_parser("list"); p = sp.add_parser("log"); p.add_argument("n", nargs="?", type=int, default=10)
    p = sp.add_parser("models"); p.add_argument("seat"); p.add_argument("grep", nargs="?", default="")
    p = sp.add_parser("ask"); p.add_argument("seat"); p.add_argument("brief"); p.add_argument("--job")
    p = sp.add_parser("note"); p.add_argument("job"); p.add_argument("text")
    p = sp.add_parser("run"); p.add_argument("brief", nargs="?"); p.add_argument("--seats", required=True); p.add_argument("--mode", default="best-of-n", choices=MODES)
    p.add_argument("--assign", action="append"); p.add_argument("--budget", type=float); p.add_argument("--job")
    p = sp.add_parser("plan"); p.add_argument("action", nargs="?", default="show"); p.add_argument("--n", type=int); p.add_argument("--mode", choices=MODES)
    g = sp.add_parser("gateway").add_subparsers(dest="sub", required=True); q = g.add_parser("add"); q.add_argument("name"); q.add_argument("--base-url", required=True); g.add_parser("ls")
    k = sp.add_parser("catalog").add_subparsers(dest="sub", required=True); q = k.add_parser("add"); q.add_argument("name"); q.add_argument("--gateway", required=True); q.add_argument("--model", required=True)
    q.add_argument("--in-cost", type=float); q.add_argument("--out-cost", type=float); k.add_parser("ls")
    q = k.add_parser("import"); q.add_argument("gateway"); q.add_argument("grep", nargs="?", default="")
    q = k.add_parser("test"); q.add_argument("name"); q = k.add_parser("price"); q.add_argument("name"); q.add_argument("--in-cost", type=float); q.add_argument("--out-cost", type=float)
    q = k.add_parser("remove"); q.add_argument("name")
    s = sp.add_parser("seat").add_subparsers(dest="sub", required=True); s.add_parser("ls"); s.add_parser("undo"); q = s.add_parser("add"); q.add_argument("name", nargs="?")
    q = s.add_parser("set"); q.add_argument("seat"); q.add_argument("catalog"); q = s.add_parser("clear"); q.add_argument("seat"); q = s.add_parser("remove"); q.add_argument("seat")
    x = ap.parse_args(); c = db()
    key = lambda who: os.environ.get("AI_TEAM_KEY") or getpass.getpass(f"API key ของ {who} (พิมพ์แล้วไม่แสดง): ")
    try:
        if x.cmd == "add":
            host = urlparse(x.base_url).netloc; cat = x.model.split("/")[-1]
            if not c.execute("select 1 from gateways where name=?", (host,)).fetchone() or os.environ.get("AI_TEAM_KEY"):
                c.execute("insert or replace into gateways values(?,?,?)", (host, x.base_url, key(host)))
            c.execute("insert or replace into catalog(name,gateway,model) values(?,?,?)", (cat, host, x.model))
            c.execute("insert or replace into seats values(?,?,?,?)", (x.name, x.title or x.name, x.role, cat)); c.commit()
            print(f"บันทึกช่อง {x.name} ← {cat}")
        elif x.cmd == "list" or (x.cmd == "seat" and x.sub == "ls"):
            for n, t, k_, m in c.execute("select s.name,s.title,s.catalog,k.model from seats s left join catalog k on k.name=s.catalog"):
                print(f"{n}\t{t}\t{k_ or '(ว่าง)'}\t{m or ''}")
        elif x.cmd == "gateway":
            if x.sub == "add":
                c.execute("insert or replace into gateways values(?,?,?)", (x.name, x.base_url, key(x.name))); c.commit(); print("บันทึก gateway", x.name)
            else:
                for n, u in c.execute("select name,base_url from gateways"): print(f"{n}\t{u}")
        elif x.cmd == "catalog":
            if x.sub == "add":
                c.execute("insert or replace into catalog(name,gateway,model,cin,cout) values(?,?,?,?,?)", (x.name, x.gateway, x.model, x.in_cost, x.out_cost)); c.commit()
            elif x.sub == "ls":
                for n, g_, m, ci, co, s_ in c.execute("select name,gateway,model,cin,cout,status from catalog"): print(f"{n}\t{g_}\t{m}\tin={ci}\tout={co}\t{s_ or '-'}")
            elif x.sub == "import":
                u, k_ = c.execute("select base_url,api_key from gateways where name=?", (x.gateway,)).fetchone()
                rq = urllib.request.Request(u.rstrip("/") + "/models", headers={"Authorization": f"Bearer {k_}", **UA})
                ids = sorted(m["id"] for m in json.load(urllib.request.urlopen(rq, timeout=60))["data"])
                add = [i for i in ids if x.grep.lower() in i.lower() and not any(w in i.lower() for w in SKIP)]
                for i in add: c.execute("insert or ignore into catalog(name,gateway,model) values(?,?,?)", (i.split("/")[-1], x.gateway, i))
                c.commit(); print(f"นำเข้า {len(add)} โมเดล (ยังไม่ทดสอบ: ใช้ catalog test)")
            elif x.sub == "test":
                print(x.name, catalog_test(c, x.name))
            elif x.sub == "price":
                c.execute("update catalog set cin=coalesce(?,cin),cout=coalesce(?,cout) where name=?", (x.in_cost, x.out_cost, x.name)); c.commit()
            else:
                c.execute("delete from catalog where name=?", (x.name,)); c.commit()
        elif x.cmd == "seat":
            if x.sub == "set": seat_set(c, x.seat, x.catalog); print(f"{x.seat} ← {x.catalog}")
            elif x.sub == "clear": seat_set(c, x.seat, None)
            elif x.sub == "undo": print("ย้อนช่อง", seat_undo(c))
            elif x.sub == "add":
                if x.name: c.execute("insert into seats values(?,?,?,null)", (x.name, x.name, "")); c.commit(); print(x.name)
                else: print(seat_add(c))
            else: c.execute("delete from seats where name=?", (x.seat,)); c.commit()
        elif x.cmd == "plan":
            if x.action == "set":
                if x.n: c.execute("insert or replace into settings values('n',?)", (str(x.n),))
                if x.mode: c.execute("insert or replace into settings values('mode',?)", (x.mode,))
                c.commit()
            n = int(setting(c, "n", 2)); act = [r[0] for r in c.execute("select s.name from seats s join catalog k on k.name=s.catalog")][:n]
            print(f"mode={setting(c, 'mode')} n={n} active={','.join(act)}")
        elif x.cmd == "log":
            for t, n, m, b, bl, j in c.execute("select t,agent,model,brief,blocked,job from audit order by t desc limit ?", (x.n,)):
                print(time.strftime("%H:%M:%S", time.localtime(t)), n, m, bl or "ok", f"[{j}]" if j else "", "|", b[:60].replace("\n", " "))
        elif x.cmd == "models":
            u, k_ = c.execute("select g.base_url,g.api_key from seats s join catalog k on k.name=s.catalog join gateways g on g.name=k.gateway where s.name=?", (x.seat,)).fetchone()
            rq = urllib.request.Request(u.rstrip("/") + "/models", headers={"Authorization": f"Bearer {k_}", **UA})
            print("\n".join(i for i in sorted(m["id"] for m in json.load(urllib.request.urlopen(rq, timeout=60))["data"]) if x.grep.lower() in i.lower()))
        elif x.cmd == "note":
            c.execute("insert into notes values(?,?,?)", (time.time(), x.job, x.text)); c.commit()
        elif x.cmd == "run":
            run(c, x)
        else:
            ok, text = call(c, x.seat, x.brief, x.job)
            print(text) if ok else sys.exit(text)
    except (ValueError, TypeError) as e:
        sys.exit(f"[ERROR] {e}")


if __name__ == "__main__":
    main()
