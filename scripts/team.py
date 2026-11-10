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
import argparse, collections, shutil, subprocess, tempfile, threading, concurrent.futures as cf, getpass, json, os, re, sqlite3, sys, time, urllib.error, urllib.request
from urllib.parse import urlparse

DB = os.environ.get("AI_TEAM_DB", os.path.expanduser("~/.ai-team/team.db"))
BLOCK = [r"sk-[A-Za-z0-9]{20,}", r"(?i)api[_-]?key\s*[:=]", r"\b\d{13}\b", r"(?i)password\s*[:=]"]
MAX_CHARS, MAX_CALLS_PER_HOUR = 8000, 60
UA = {"User-Agent": "ai-team/1.0"}
PLACEHOLDER = re.compile(r"(?i)xxx\.|your-|url-|example\.|<.*>")
MODES = ("best-of-n", "vote", "split")
SKIP = ("embed", "safety", "guard", "parse", "reward", "rerank", "vl", "content", "whisper", "tts", "orpheus", "playai", "transcribe", "moderation")
SCHEMA = """
create table if not exists agents(name text primary key,title text,base_url text,model text,api_key text,role text);
create table if not exists audit(t real,agent text,model text,brief text,reply text,blocked text);
create table if not exists notes(t real,job text,text text);
create table if not exists gateways(name text primary key,base_url text,api_key text);
create table if not exists catalog(name text primary key,gateway text,model text,cin real,cout real,status text);
create table if not exists seats(name text primary key,title text,role text,catalog text);
create table if not exists seat_log(t real,seat text,old text,new text);
create table if not exists settings(k text primary key,v text);
create table if not exists roles(name text primary key,title text,prompt text,kind text);
create table if not exists tasks(id integer primary key autoincrement,job text,pool text,brief text,chk text,status text,result text,error text,attempts integer default 0,used_seat text,created real,started real,finished real);
"""

BUILTIN_ROLES = [
    ("maker", "ผู้ทำ", "คุณคือผู้ทำงาน: ทำตามโจทย์ให้ครบทุกข้อ ถูกต้อง กระชับ ถ้าได้รับข้อบกพร่องจากผู้ตรวจให้แก้ทุกข้อและตอบเป็นงานฉบับแก้ไขเต็ม", "maker"),
    ("reviewer", "ผู้ตรวจ", "คุณคือผู้ตรวจงานที่เข้มงวดและเป็นกลาง: ตรวจงานเทียบกับโจทย์ทีละข้อ ห้ามให้ผ่านถ้ายังไม่แน่ใจ ทุกข้อบกพร่องต้องมีหลักฐานจากตัวงาน อย่าชมเกินจริง ตอบเป็น JSON เท่านั้น", "reviewer"),
    ("tester", "ผู้ทดสอบ", "คุณคือผู้ทดสอบซอฟต์แวร์: ออกแบบเคสทดสอบทั้งปกติ เคสขอบ และเคสผิดพลาด ระบุขั้นตอนและผลที่คาดหวังให้ชัดเจน", "maker"),
    ("translator", "ผู้แปล/สรุป", "คุณคือผู้แปลและสรุป: ถ่ายทอดเนื้อหาให้ครบถ้วนไม่เพิ่มข้อมูลเอง ใช้ภาษาไทยที่เป็นธรรมชาติ", "maker"),
    ("cleaner", "ผู้ล้างข้อมูล", "คุณคือผู้ล้างข้อมูล: ตรวจรูปแบบ ชื่อ เบอร์โทร เลขภาษี ที่อยู่ไทย หาค่าที่ซ้ำหรือผิดปกติ และรายงานตามที่พบเท่านั้น ห้ามเดาค่าที่ไม่มีในข้อมูล", "maker"),
    ("analyst", "ผู้วิเคราะห์", "คุณคือผู้วิเคราะห์: สรุปประเด็นสำคัญ ข้อเท็จจริงที่ยืนยันได้ แยกจากข้อสันนิษฐาน และบอกสิ่งที่ยังไม่รู้", "maker"),
]


def ensure_roles(c):
    for r in BUILTIN_ROLES:
        c.execute("insert or ignore into roles values(?,?,?,?)", r)
    c.commit()


def role_prompt(c, name):
    r = c.execute("select prompt from roles where name=?", (name,)).fetchone()
    if not r:
        raise ValueError(f"ไม่พบตำแหน่ง '{name}' (ดู: team.py role ls)")
    return r[0]


def set_position(c, seat, role):
    """ตั้งตำแหน่งให้ช่อง: ใช้คำสั่งบทบาทของตำแหน่งนั้นเป็นค่าเริ่มต้นของช่อง (role=None = ล้าง)"""
    if not c.execute("select 1 from seats where name=?", (seat,)).fetchone():
        raise ValueError(f"ไม่พบช่อง '{seat}'")
    if role:
        c.execute("update seats set position=?, role=? where name=?", (role, role_prompt(c, role), seat))
    else:
        c.execute("update seats set position=null, role=? where name=?", ("ผู้ช่วย AI: ทำงานตามที่ได้รับมอบหมาย กระชับ ถูกต้อง", seat))
    c.commit()



def cols(c, t):
    return [r[1] for r in c.execute(f"pragma table_info({t})")]


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.executescript(SCHEMA)
    for col, typ in (("job", "text"), ("ms", "integer")):
        if col not in cols(c, "audit"):
            c.execute(f"alter table audit add column {col} {typ}")
    for t_, col, typ in (("seats", "position", "text"), ("tasks", "flow", "text"), ("tasks", "rounds", "integer"),
                         ("tasks", "trace", "text"), ("tasks", "asrole", "text")):
        if col not in cols(c, t_):
            c.execute(f"alter table {t_} add column {col} {typ}")
    ensure_roles(c)
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
        c.execute("insert or ignore into seats(name,title,role,catalog) values(?,?,?,?)", (name, title, role, model.split("/")[-1]))
    for i in (3, 4):
        c.execute("insert or ignore into seats(name,title,role,catalog) values(?,?,?,null)", (f"seat{i}", f"ช่อง {i}", "ผู้ช่วย AI: ทำงานตามที่ได้รับมอบหมาย กระชับ ถูกต้อง"))
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


def call(c, seat, brief, job=None, role_ov=None):
    """คืน (ok, text) ไม่ throw/exit — ใช้ได้ทั้ง ask และ run"""
    r = resolve(c, seat)
    if not r:
        return False, f"[ERROR] ช่อง '{seat}' ไม่มีหรือยังว่าง (ดู: team.py seat ls)"
    role, model, url, key, _, _, _ = r
    if role_ov is not None:
        role = role_ov
    why = None
    if PLACEHOLDER.search(url):
        why = "base_url ยังเป็นค่าตัวอย่าง ห้ามส่ง key ไป"
    elif len(brief) > int(setting(c, "max_chars", MAX_CHARS)):
        why = "brief ยาวเกินกำหนด"
    elif any(re.search(p, brief) for p in BLOCK):
        why = "brief มีข้อมูลต้องห้าม (key/รหัสผ่าน/เลข 13 หลัก)"
    elif c.execute("select count(*) from audit where t>? and blocked is null", (time.time() - 3600,)).fetchone()[0] >= int(setting(c, "max_calls_per_hour", MAX_CALLS_PER_HOUR)):
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
    c.execute("insert into seats(name,title,role,catalog) values(?,?,?,null)", (f"seat{n}", f"ช่อง {n}", "ผู้ช่วย AI: ทำงานตามที่ได้รับมอบหมาย กระชับ ถูกต้อง")); c.commit()
    return f"seat{n}"


def valid_base_url(u):
    u = (u or "").strip().rstrip("/")
    if PLACEHOLDER.search(u):
        raise ValueError("base_url ยังเป็นค่าตัวอย่าง")
    p = urlparse(u)
    if p.scheme == "https" and p.netloc:
        return u
    if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1"):
        return u
    raise ValueError("base_url ต้องขึ้นต้นด้วย https:// (หรือ http://localhost สำหรับทดสอบในเครื่อง)")


def remote_models(c, gw):
    """ดึงรายชื่อโมเดลจากเกตเวย์ (ใช้ key ในฐานข้อมูล ไม่คืน key)"""
    r = c.execute("select base_url,api_key from gateways where name=?", (gw,)).fetchone()
    if not r:
        raise ValueError(f"ไม่พบ gateway '{gw}'")
    rq = urllib.request.Request(r[0].rstrip("/") + "/models", headers={"Authorization": f"Bearer {r[1]}", **UA})
    try:
        data = json.load(urllib.request.urlopen(rq, timeout=40))["data"]
    except urllib.error.HTTPError as e:
        raise ValueError({401: "API key ไม่ถูกต้อง", 403: "key ไม่มีสิทธิ์หรือถูกปฏิเสธ", 404: "ไม่พบ /models ที่ URL นี้ (ตรวจ Base URL)"}.get(e.code, f"HTTP {e.code}"))
    except Exception as e:
        raise ValueError("เชื่อมต่อไม่ได้: " + type(e).__name__)
    return sorted(m["id"] for m in data)


def import_models(c, gw, grep=""):
    ids = remote_models(c, gw)
    add = [i for i in ids if grep.lower() in i.lower() and not any(w in i.lower() for w in SKIP)]
    before = c.total_changes
    for i in add:
        c.execute("insert or ignore into catalog(name,gateway,model) values(?,?,?)", (i.split("/")[-1], gw, i))
    c.commit()
    return c.total_changes - before, len(add), len(ids)


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

# ---------- คิวงานแบบ async: ส่งงานทีเดียวหลายชิ้น ให้ worker ทำขนานกัน (กันคอขวดที่ Claude) ----------
def expand_pool(c, names):
    """@ชื่อ -> รายชื่อช่องที่ตั้งไว้ด้วย team.py pool set"""
    out = []
    for n in names:
        if n.startswith("@"):
            v = setting(c, "pool:" + n[1:])
            if v is None:
                raise ValueError(f"ไม่พบกลุ่มช่อง '{n}' (ดู: team.py pool ls)")
            out += [x for x in v.split(",") if x]
        elif n:
            out.append(n)
    return out


def submit(c, brief, pool, job=None, chk=None, flow=None, rounds=0, asrole=None):
    pool = expand_pool(c, (pool or "").split(","))
    if flow:
        for st in flow:
            st["pool"] = expand_pool(c, st["pool"])
        pool = [x for st in flow for x in st["pool"]]
    if asrole:
        role_prompt(c, asrole)
    for sname in pool:
        if not c.execute("select 1 from seats where name=?", (sname,)).fetchone():
            raise ValueError(f"ไม่พบช่อง '{sname}'")
    tid = c.execute("insert into tasks(job,pool,brief,chk,status,created,flow,rounds,asrole) values(?,?,?,?,'queued',?,?,?,?)",
                    (job, ",".join(pool), brief, chk, time.time(), json.dumps(flow, ensure_ascii=False) if flow else None, rounds, asrole)).lastrowid
    c.commit()
    return tid


def run_check(chk, text):
    """ตรวจคำตอบอัตโนมัติก่อนถึงมือ Claude: json | contains:X | regex:R | notempty"""
    if not chk:
        return True, ""
    kind, _, arg = chk.partition(":")
    if kind == "notempty":
        ok = bool(text.strip()); return ok, "" if ok else "ว่าง"
    if kind == "contains":
        ok = arg.lower() in text.lower(); return ok, "" if ok else f"ไม่พบ '{arg}'"
    if kind == "regex":
        ok = bool(re.search(arg, text, re.S)); return ok, "" if ok else f"ไม่ตรง regex {arg}"
    if kind == "py":
        return run_py_check(arg, text)
    if kind == "spec":
        import odoo_spec
        return odoo_spec.validate(text)
    if kind == "json":
        return (True, "") if first_json(text) is not None else (False, "ไม่ใช่ JSON")
    return True, ""


def run_py_check(test_path, text):
    """รันโค้ดที่โมเดลเขียน (code block แรก เป็น solution.py) กับสคริปต์เทสของเรา ในกล่องทราย macOS:
    ห้ามเน็ตเวิร์ก ห้ามเขียนไฟล์นอกโฟลเดอร์ชั่วคราว จำกัดเวลา 15 วินาที"""
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = m.group(1) if m else text
    if not os.path.isfile(test_path):
        return False, f"ไม่พบสคริปต์เทส {test_path}"
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        open(os.path.join(d, "solution.py"), "w", encoding="utf-8").write(code)
        shutil.copy(test_path, os.path.join(d, "test_solution.py"))
        prof = f'(version 1)(allow default)(deny network*)(deny file-write* (subpath "/"))(allow file-write* (subpath "{d}"))(allow file-write* (literal "/dev/null"))'
        try:
            r = subprocess.run(["sandbox-exec", "-p", prof, sys.executable, "-E", "-s", "test_solution.py"], capture_output=True, text=True, timeout=15, cwd=d)
        except Exception as e:
            return False, f"รันเทสไม่ได้: {type(e).__name__}"
        return r.returncode == 0, "" if r.returncode == 0 else ((r.stderr or r.stdout).strip()[-300:] or "เทสล้ม")


def claim(c):
    c.execute("begin immediate")
    r = c.execute("select id,job,pool,brief,chk,attempts,flow,rounds,asrole from tasks where status='queued' order by id limit 1").fetchone()
    if r:
        c.execute("update tasks set status='running',started=?,attempts=attempts+1 where id=?", (time.time(), r[0]))
    c.commit()
    return r


def parse_flow(spec):
    """'maker@worker,seat3>reviewer@intern,seat4' -> [{"role":"maker","pool":[...]}, ...]"""
    out = []
    for part in spec.split(">"):
        role, _, pool = part.strip().partition("@")
        if not role or not pool:
            raise ValueError(f"รูปแบบ flow ผิด: '{part}' (ต้องเป็น ตำแหน่ง@ช่อง1,ช่อง2)")
        out.append({"role": role.strip(), "pool": [x.strip() for x in pool.split(",") if x.strip()]})
    return out


def try_pool(c, pool, brief, job, role_ov, sems, busy, lock, exclude=(), avoid_gw=None):
    """ลองช่องใน pool ตามลำดับ (เกตเวย์ว่างสุด/ต่างเกตเวย์จาก avoid_gw ก่อน) คืน (ok, text, seat, err)"""
    gw = lambda sname: (c.execute("select k.gateway from seats s join catalog k on k.name=s.catalog where s.name=?", (sname,)).fetchone() or ["?"])[0]
    cands = [x for x in pool if x not in exclude] or list(pool)
    last = ""
    for rnd in range(2):
        with lock:
            order = sorted(cands, key=lambda x: (gw(x) == avoid_gw, busy.get(gw(x), 0)))
        for sname in order:
            g = gw(sname)
            with lock:
                sems.setdefault(g, threading.Semaphore(sems["_per_gw"]))
                busy[g] = busy.get(g, 0) + 1
            try:
                with sems[g]:
                    ok, text = call(c, sname, brief, job, role_ov)
            finally:
                with lock:
                    busy[g] -= 1
            if ok:
                return True, text, sname, ""
            last = text
            if text.startswith("[BLOCKED]"):
                return False, text, sname, "blocked"
        time.sleep(8)
    return False, last, None, "error"


def first_json(text, want=None):
    """คืนวัตถุ JSON ตัวแรกที่ parse ได้ในข้อความ (want = ชนิด dict/list ที่ต้องการ)"""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if want is None or isinstance(obj, want):
                return obj
    return None


def parse_verdict(text):
    cands = []
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict) and "verdict" in obj:
                cands.append(obj)
    if not cands:
        return False, {"verdict": "unparsable", "issues": ["ผู้ตรวจไม่ได้ตอบเป็น JSON: " + text[:120]]}
    d = cands[0]
    v = str(d.get("verdict", "")).strip().lower()
    return v in ("pass", "ผ่าน"), d


def run_flow(c, t, sems, busy, lock):
    tid, job, pool, brief, chk, _, flow, rounds, asrole = t
    stages = json.loads(flow)
    mk, rv = stages[0], (stages[1] if len(stages) > 1 else None)
    trace, out, issues, mseat = [], "", "", None
    for rnd in range(int(rounds or 0) + 1):
        prompt = brief if rnd == 0 else f"{brief}\n\n--- งานเดิมของคุณ\n{out}\n\n--- ข้อบกพร่องที่ผู้ตรวจพบ (แก้ให้ครบทุกข้อแล้วส่งงานฉบับเต็ม)\n{issues}"
        ok, text, mseat, err = try_pool(c, mk["pool"], prompt, job, role_prompt(c, mk["role"]), sems, busy, lock)
        trace.append({"stage": mk["role"], "round": rnd, "seat": mseat, "ok": ok})
        if not ok:
            c.execute("update tasks set status=?,error=?,trace=?,finished=? where id=?", (err or "error", text[:300], json.dumps(trace, ensure_ascii=False), time.time(), tid)); c.commit()
            return
        out = text
        if not rv:
            if chk:
                good_, why_ = run_check(chk, out)
                trace.append({"stage": "check", "round": rnd, "verdict": "pass" if good_ else "fail", "issues": [why_] if why_ else None})
                if not good_ and rnd < int(rounds or 0):
                    issues = why_
                    continue
                if not good_:
                    c.execute("update tasks set status='rejected',result=?,error=?,trace=?,used_seat=?,finished=? where id=?", (out, why_[:300], json.dumps(trace, ensure_ascii=False), mseat, time.time(), tid)); c.commit()
                    return
            break
        mgw = (c.execute("select k.gateway from seats s join catalog k on k.name=s.catalog where s.name=?", (mseat,)).fetchone() or ["?"])[0]
        rprompt = (f"โจทย์:\n{brief}\n\nงานที่ต้องตรวจ:\n{out}\n\nตรวจเทียบโจทย์ทีละข้อ ตอบเป็น JSON เท่านั้น: "
                   '{"verdict":"pass" หรือ "fail","score":0-10,"issues":["ข้อบกพร่อง"],"evidence":["หลักฐานจากตัวงาน"]}')
        ok2, rtext, rseat, err2 = try_pool(c, rv["pool"], rprompt, job, role_prompt(c, rv["role"]), sems, busy, lock, exclude={mseat}, avoid_gw=mgw)
        if not ok2:
            trace.append({"stage": rv["role"], "round": rnd, "seat": rseat, "ok": False})
            c.execute("update tasks set status='error',result=?,error=?,trace=?,used_seat=?,finished=? where id=?", (out, "ผู้ตรวจล้ม: " + rtext[:200], json.dumps(trace, ensure_ascii=False), mseat, time.time(), tid)); c.commit()
            return
        passed, d = parse_verdict(rtext)
        if d.get("verdict") == "unparsable":  # ผู้ตรวจตอบว่าง/ไม่ใช่ JSON (โมเดล reasoning ชน token) -> ให้ผู้ตรวจอีกช่องตรวจแทน ไม่นับเป็น fail
            ok3, rtext2, rseat2, _ = try_pool(c, rv["pool"], rprompt, job, role_prompt(c, rv["role"]), sems, busy, lock, exclude={mseat, rseat}, avoid_gw=mgw)
            if ok3 and rseat2 != rseat:
                trace.append({"stage": rv["role"], "round": rnd, "seat": rseat, "verdict": "unparsable"})
                passed, d = parse_verdict(rtext2)
                rseat = rseat2
        trace.append({"stage": rv["role"], "round": rnd, "seat": rseat, "verdict": "pass" if passed else "fail", "score": d.get("score"), "issues": d.get("issues")})
        if passed:
            break
        issues = "\n".join(f"- {x}" for x in (d.get("issues") or ["(ผู้ตรวจไม่ระบุ)"]))
    else:
        c.execute("update tasks set status='rejected',result=?,error=?,trace=?,used_seat=?,finished=? where id=?", (out, issues[:300], json.dumps(trace, ensure_ascii=False), mseat, time.time(), tid)); c.commit()
        return
    good, why = run_check(chk, out)
    c.execute("update tasks set status=?,result=?,error=?,trace=?,used_seat=?,finished=? where id=?",
              ("done" if good else "check_failed", out, why, json.dumps(trace, ensure_ascii=False), mseat, time.time(), tid)); c.commit()


def process(c, t, sems, busy, lock):
    tid, job, pool, brief, chk, _, flow, rounds, asrole = t
    if flow:
        return run_flow(c, t, sems, busy, lock)
    ov = role_prompt(c, asrole) if asrole else None
    ok, text, seat, err = try_pool(c, pool.split(","), brief, job, ov, sems, busy, lock)
    if ok:
        good, why = run_check(chk, text)
        c.execute("update tasks set status=?,result=?,error=?,used_seat=?,finished=? where id=?", ("done" if good else "check_failed", text, why, seat, time.time(), tid))
    elif err == "blocked":
        c.execute("update tasks set status='blocked',error=?,used_seat=?,finished=? where id=?", (text, seat, time.time(), tid))
    else:
        c.execute("update tasks set status='error',error=?,finished=? where id=?", (text[:300], time.time(), tid))
    c.commit()


def worker(concurrency=8, per_gw=3, drain=True):
    sems, busy, lock = {"_per_gw": per_gw}, {}, threading.Lock()
    state = {"active": 0}
    t0 = time.time()

    def loop():
        c = db()
        while True:
            t = claim(c)
            if not t:
                with lock:
                    if drain and state["active"] == 0:
                        return
                time.sleep(1); continue
            with lock:
                state["active"] += 1
            try:
                process(c, t, sems, busy, lock)
            finally:
                with lock:
                    state["active"] -= 1
    ths = [threading.Thread(target=loop, daemon=True) for _ in range(concurrency)]
    [th.start() for th in ths]
    [th.join() for th in ths]
    c = db()
    cnt = dict(c.execute("select status,count(*) from tasks where created>=? group by status", (t0 - 1,)).fetchall())
    print(f"worker เสร็จใน {time.time() - t0:.1f}s · {cnt}")


def queue_counts(c, job=None):
    q = "select status,count(*) from tasks" + (" where job=?" if job else "") + " group by status"
    return dict(c.execute(q, (job,) if job else ()).fetchall())


def wait_job(c, job, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cnt = queue_counts(c, job)
        if not cnt.get("queued") and not cnt.get("running"):
            print(f"[เสร็จ {time.time() - t0:.0f}s] {cnt}"); return
        time.sleep(2)
    print(f"[หมดเวลา] {queue_counts(c, job)}")


def show_results(c, job=None, status=None, full=None):
    if full:
        r = c.execute("select id,job,used_seat,status,error,result,brief from tasks where id=?", (full,)).fetchone()
        print(f"#{r[0]} [{r[1]}] {r[2]} {r[3]} {r[4] or ''}\n--- brief\n{r[6]}\n--- result\n{r[5]}"); return
    w, a = [], []
    if job: w.append("job=?"); a.append(job)
    if status: w.append("status=?"); a.append(status)
    for i, j, st, seat, err, res, ms, tr in c.execute("select id,job,status,used_seat,error,result,(finished-started),trace from tasks" + (" where " + " and ".join(w) if w else "") + " order by id", a):
        one = (res or "").strip().replace("\n", " ")[:90]
        flow_txt = ""
        if tr:
            flow_txt = " ⟨" + "→".join(f"{x['stage']}({x.get('seat')})" + (f":{x['verdict']}" if x.get('verdict') else "") for x in json.loads(tr)) + "⟩"
        print(f"#{i}\t{st}\t{seat or '-'}\t{(ms or 0):.1f}s\t{('⚠ ' + err + ' | ') if err else ''}{one}{flow_txt}")



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
    q = s.add_parser("position"); q.add_argument("seat"); q.add_argument("role")
    q = s.add_parser("title"); q.add_argument("seat"); q.add_argument("text")
    q = s.add_parser("set"); q.add_argument("seat"); q.add_argument("catalog"); q = s.add_parser("clear"); q.add_argument("seat"); q = s.add_parser("remove"); q.add_argument("seat")
    p = sp.add_parser("submit"); p.add_argument("brief"); p.add_argument("--pool"); p.add_argument("--job"); p.add_argument("--check")
    p.add_argument("--flow"); p.add_argument("--rounds", type=int, default=1); p.add_argument("--as", dest="asrole")
    p = sp.add_parser("batch"); p.add_argument("file"); p.add_argument("--pool"); p.add_argument("--job"); p.add_argument("--check")
    p.add_argument("--flow"); p.add_argument("--rounds", type=int, default=1); p.add_argument("--as", dest="asrole")
    r_ = sp.add_parser("role").add_subparsers(dest="sub", required=True); r_.add_parser("ls"); q = r_.add_parser("add"); q.add_argument("name"); q.add_argument("--title", required=True)
    q.add_argument("--prompt", required=True); q.add_argument("--kind", default="maker"); q = r_.add_parser("rm"); q.add_argument("name")
    p = sp.add_parser("worker"); p.add_argument("--concurrency", type=int, default=8); p.add_argument("--per-gateway", type=int, default=3); p.add_argument("--keep", action="store_true")
    p = sp.add_parser("wait"); p.add_argument("job", nargs="?"); p.add_argument("--timeout", type=int, default=900)
    p = sp.add_parser("results"); p.add_argument("--job"); p.add_argument("--status"); p.add_argument("--full", type=int)
    sp.add_parser("queue")
    p = sp.add_parser("limit"); p.add_argument("n", nargs="?", type=int); p.add_argument("--chars", type=int)
    p = sp.add_parser("pool"); p.add_argument("action", choices=["set", "ls", "rm"]); p.add_argument("name", nargs="?"); p.add_argument("seats", nargs="*")
    x = ap.parse_args(); c = db()
    key = lambda who: os.environ.get("AI_TEAM_KEY") or getpass.getpass(f"API key ของ {who} (พิมพ์แล้วไม่แสดง): ")
    try:
        if x.cmd == "add":
            host = urlparse(x.base_url).netloc; cat = x.model.split("/")[-1]
            if not c.execute("select 1 from gateways where name=?", (host,)).fetchone() or os.environ.get("AI_TEAM_KEY"):
                c.execute("insert or replace into gateways values(?,?,?)", (host, x.base_url, key(host)))
            c.execute("insert or replace into catalog(name,gateway,model) values(?,?,?)", (cat, host, x.model))
            c.execute("insert or replace into seats(name,title,role,catalog) values(?,?,?,?)", (x.name, x.title or x.name, x.role, cat)); c.commit()
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
            if x.sub == "title":
                c.execute("update seats set title=? where name=?", (x.text, x.seat)); c.commit(); print(x.seat, "ชื่อตำแหน่ง:", x.text)
            elif x.sub == "position": set_position(c, x.seat, None if x.role == "-" else x.role); print(f"{x.seat} ตำแหน่ง: {x.role}")
            elif x.sub == "set": seat_set(c, x.seat, x.catalog); print(f"{x.seat} ← {x.catalog}")
            elif x.sub == "clear": seat_set(c, x.seat, None)
            elif x.sub == "undo": print("ย้อนช่อง", seat_undo(c))
            elif x.sub == "add":
                if x.name: c.execute("insert into seats(name,title,role,catalog) values(?,?,?,null)", (x.name, x.name, "")); c.commit(); print(x.name)
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
        elif x.cmd == "role":
            if x.sub == "ls":
                for n, t_, pr, k_ in c.execute("select name,title,prompt,kind from roles"): print(f"{n}\t{t_}\t[{k_}]\t{pr[:60]}")
            elif x.sub == "add":
                c.execute("insert or replace into roles values(?,?,?,?)", (x.name, x.title, x.prompt, x.kind)); c.commit(); print("บันทึกตำแหน่ง", x.name)
            else:
                c.execute("delete from roles where name=?", (x.name,)); c.commit()
        elif x.cmd == "submit":
            print(submit(c, sys.stdin.read() if x.brief == "-" else x.brief, x.pool, x.job, x.check, parse_flow(x.flow) if x.flow else None, x.rounds if x.flow else 0, x.asrole))
        elif x.cmd == "batch":
            n = 0
            for line in open(x.file, encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                fl = d.get("flow") or x.flow
                submit(c, d["brief"], d.get("pool") or x.pool, d.get("job") or x.job, d.get("check") or x.check,
                       parse_flow(fl) if fl else None, d.get("rounds", x.rounds) if fl else 0, d.get("as") or x.asrole); n += 1
            print(f"ส่งเข้าคิว {n} งาน")
        elif x.cmd == "worker":
            worker(x.concurrency, x.per_gateway, drain=not x.keep)
        elif x.cmd == "wait":
            wait_job(c, x.job, x.timeout)
        elif x.cmd == "results":
            show_results(c, x.job, x.status, x.full)
        elif x.cmd == "queue":
            print(queue_counts(c))
        elif x.cmd == "limit":
            if x.n:
                c.execute("insert or replace into settings values('max_calls_per_hour',?)", (str(x.n),)); c.commit()
            if x.chars:
                c.execute("insert or replace into settings values('max_chars',?)", (str(x.chars),)); c.commit()
            print("เพดานเรียกโมเดล/ชั่วโมง =", setting(c, "max_calls_per_hour", MAX_CALLS_PER_HOUR), "| ความยาว brief สูงสุด (ตัวอักษร) =", setting(c, "max_chars", MAX_CHARS))
        elif x.cmd == "pool":
            if x.action == "set":
                for sn in x.seats:
                    if not c.execute("select 1 from seats where name=?", (sn,)).fetchone():
                        raise ValueError(f"ไม่พบช่อง '{sn}'")
                c.execute("insert or replace into settings values(?,?)", ("pool:" + x.name, ",".join(x.seats))); c.commit(); print(f"@{x.name} = {','.join(x.seats)}")
            elif x.action == "rm":
                c.execute("delete from settings where k=?", ("pool:" + x.name,)); c.commit()
            else:
                for k_, v_ in c.execute("select k,v from settings where k like 'pool:%'"): print(f"@{k_[5:]}\t{v_}")
        elif x.cmd == "run":
            run(c, x)
        else:
            ok, text = call(c, x.seat, x.brief, x.job)
            print(text) if ok else sys.exit(text)
    except (ValueError, TypeError) as e:
        sys.exit(f"[ERROR] {e}")


if __name__ == "__main__":
    main()
