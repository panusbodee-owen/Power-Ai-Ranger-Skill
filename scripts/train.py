# -*- coding: utf-8 -*-
"""train.py — วัดผลและ "เทรน" สมาชิกทีม AI ด้วยคู่มือสั่งงาน (coaching prompt)
ใช้โมเดลแบบโฮสต์ ไม่มีการ fine-tune: เทรน = เขียนคำแนะนำเพิ่มในพรอมต์ จากจุดที่พลาดในชุด A แล้ววัดซ้ำบนชุด B (คนละโจทย์)
ใช้:  python3 train.py run [--repeats 2]   รันทั้งกระบวนการ (ต่อจากที่ค้างได้)
      python3 train.py report OUT.md        สร้างรายงาน Performance
ผลเก็บในตาราง train_runs / seat_coach (ไม่แตะ seat_skills โดยตรง)
"""
import json, os, re, sqlite3, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team

DB = team.DB
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
THAI = re.compile(r"[฀-๿]")
PER_HOUR_CAP = 550          # ผู้ใช้อนุญาตให้เพิ่มเพดานชั่วคราว (setting max_calls_per_hour=600; เดิม 200)
GW_SEM = {}


def clean(t):
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    if "</think>" in t:
        t = t.split("</think>")[-1]
    return t.strip()


def parse_json_strict(t):
    t = clean(t)
    if not t or t[0] not in "[{":
        return None, "fence_or_extra"
    try:
        return json.loads(t), None
    except Exception:
        return None, "json_invalid"


# ---------- graders: คืน (ok, tag) ----------
def g_digits(ans):
    def f(t):
        t = clean(t)
        if not t:
            return False, "empty"
        if CJK.search(t):
            return False, "cjk"
        if re.fullmatch(r"[0-9,.\s]+", t) and re.sub(r"[^0-9]", "", t) == ans:
            return True, None
        return (False, "extra_text") if ans in t else (False, "wrong_value")
    return f


def g_exact(ans):
    def f(t):
        t = clean(t).strip(" .`\n")
        if not t:
            return False, "empty"
        if t == ans:
            return True, None
        return (False, "extra_text") if ans in t else (False, "wrong_value")
    return f


def g_extract(exp):
    def f(t):
        d, tag = parse_json_strict(t)
        if d is None:
            return False, tag if clean(t) else "empty"
        try:
            ok = all(str(d.get(k)).replace(",", "") in (str(v), str(float(v)) if isinstance(v, (int, float)) else str(v)) for k, v in exp.items()) if False else \
                 (d.get("invoice") == exp["invoice"] and d.get("customer") == exp["customer"] and abs(float(d.get("amount")) - exp["amount"]) < 0.01 and d.get("due") == exp["due"])
        except Exception:
            ok = False
        return (True, None) if ok else (False, "wrong_value")
    return f


def g_lines3(t):
    t = clean(t)
    if not t:
        return False, "empty"
    if CJK.search(t):
        return False, "cjk"
    ls = [x for x in t.splitlines() if x.strip()]
    if len(ls) == 3 and all(x.startswith("- ") for x in ls):
        return True, None
    return False, "format"


def g_en_one(t):
    t = clean(t)
    if not t:
        return False, "empty"
    if CJK.search(t):
        return False, "cjk"
    if THAI.search(t) or len(t.splitlines()) > 1 or len(t) > 200:
        return False, "format"
    return True, None


def g_code(fn, tests):
    def f(t):
        t = clean(t)
        if not t:
            return False, "empty"
        m = re.search(r"```(?:python)?\n(.*?)```", t, re.S)
        code = m.group(1) if m else t
        if CJK.search(code):
            return False, "cjk"
        prog = code + "\n\nimport sys\nbad=0\n" + "".join(f"\ntry:\n    r={fn}{a}\n    bad+= (r!={e!r})\nexcept Exception:\n    bad+=1\n" for a, e in tests) + "\nsys.exit(1 if bad else 0)\n"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.py")
            open(p, "w", encoding="utf-8").write(prog)
            try:
                r = subprocess.run([sys.executable, "-I", p], cwd=d, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                return False, "code_timeout"
        return (True, None) if r.returncode == 0 else (False, "code_wrong")
    return f


def g_verdict(expect):
    def f(t):
        d, tag = parse_json_strict(t)
        if d is None:
            return False, tag if clean(t) else "empty"
        if isinstance(d, dict) and isinstance(d.get("pass"), bool):
            return (True, None) if d["pass"] == expect else (False, "review_wrong")
        return False, "json_invalid"
    return f


def g_summary(words):
    def f(t):
        t = clean(t)
        if not t:
            return False, "empty"
        if CJK.search(t):
            return False, "cjk"
        if len(t) > 150:
            return False, "format"
        return (True, None) if all(w in t for w in words) else (False, "missing_words")
    return f


def g_list12(key):
    def f(t):
        d, tag = parse_json_strict(t)
        if d is None:
            return False, tag if clean(t) else "empty"
        if isinstance(d, dict):
            vs = [v for v in d.values() if isinstance(v, list)]
            d = vs[0] if vs else d
        try:
            ok = isinstance(d, list) and len(d) == 12 and [x["id"] for x in d] == list(range(1, 13)) and all(isinstance(x.get(key), str) and x[key] for x in d)
        except Exception:
            ok = False
        return (True, None) if ok else (False, "format")
    return f


REV = lambda task, ans: f"โจทย์ที่ผู้ทำได้รับ: {task}\nงานที่ผู้ทำส่งมา: {ans}\n\nตัดสินว่างานนี้ผ่านโจทย์หรือไม่ ตอบ JSON ล้วนรูปแบบ {{\"pass\": true หรือ false, \"reasons\": [\"...\"]}}"
SYN = lambda pcs, req: "งานย่อยที่ทำแยกกัน:\n" + "\n".join(f"{i+1}) {p}" for i, p in enumerate(pcs)) + "\n\n" + req

# (id, skill, base_role, prompt, grader) — ชุด A ใช้เขียนคำแนะนำ ชุด B ใช้วัดผล (คนละตัวเลข/ข้อความ)
SETS = {
 "A": [
  ("short_num", "short_answer", "maker", "คำนวณ 17*23 ตอบเฉพาะตัวเลขอย่างเดียว", g_digits("391")),
  ("short_date", "short_answer", "maker", "วันที่ 2026-11-30 บวก 45 วัน คือวันที่เท่าไร ตอบรูปแบบ YYYY-MM-DD อย่างเดียว", g_exact("2027-01-14")),
  ("extract", "analysis", "analyst", "ดึงข้อมูลจากข้อความนี้ ตอบ JSON ล้วนคีย์ invoice, customer, amount (ตัวเลข), due (YYYY-MM-DD ค.ศ.):\nใบแจ้งหนี้เลขที่ INV-0042 ลูกค้า บริษัท ทรายทอง จำกัด ยอด 12,500.50 บาท ครบกำหนด 15 ธ.ค. 2569", g_extract({"invoice": "INV-0042", "customer": "บริษัท ทรายทอง จำกัด", "amount": 12500.5, "due": "2026-12-15"})),
  ("lines3", "instruction", "maker", "เขียนข้อดีของการเก็บเงินตรงเวลา 3 ข้อ เป็นรายการ 3 บรรทัดพอดี ขึ้นต้นทุกบรรทัดด้วย '- ' ห้ามมีข้อความอื่น ห้ามใช้อักษรจีน", g_lines3),
  ("en_one", "instruction", "maker", "Answer in one English sentence under 25 words, no Thai characters: what is an invoice?", g_en_one),
  ("code", "code", "maker", "เขียนฟังก์ชัน Python `def aging_bucket(days_overdue: int) -> str` คืน 'current' ถ้า days_overdue<=0, '1-30', '31-60', '61-90' ตามช่วงวัน และ '>90' ถ้ามากกว่า 90 ตอบเป็นโค้ดล้วน",
   g_code("aging_bucket", [("(0)", "current"), ("(-3)", "current"), ("(1)", "1-30"), ("(30)", "1-30"), ("(31)", "31-60"), ("(60)", "31-60"), ("(61)", "61-90"), ("(90)", "61-90"), ("(91)", ">90")])),
  ("rev_bad", "review", "reviewer", REV("คำนวณ 17*23 ตอบเฉพาะตัวเลขอย่างเดียว", "401"), g_verdict(False)),
  ("rev_good", "review", "reviewer", REV("ตอบ 1 ประโยคภาษาไทย: ข้อดีของการเก็บเงินตรงเวลา", "ช่วยให้กระแสเงินสดของธุรกิจมั่นคงและวางแผนรายจ่ายได้แม่นยำขึ้น"), g_verdict(True)),
  ("synth", "synthesis", "editor", SYN(["ลูกหนี้รายใหญ่ 3 รายจ่ายช้าเกินเครดิตเทอมเฉลี่ย 20 วัน", "ฝ่ายขายยังไม่มีระบบติดตามวันครบกำหนด", "ลูกค้าส่วนใหญ่จ่ายตรงเวลาถ้าได้รับการเตือนก่อน"], "รวมเป็นสรุปเดียวไม่เกิน 150 ตัวอักษร ต้องมีคำว่า 'ลูกหนี้' 'ครบกำหนด' 'ติดตาม'"), g_summary(["ลูกหนี้", "ครบกำหนด", "ติดตาม"])),
  ("long12", "long_output", "maker", "สร้าง JSON array 12 รายการ แต่ละรายการมีคีย์ id (1 ถึง 12 เรียงลำดับ) และ name (ชื่อผลไม้ไทย ภาษาไทย) ตอบ JSON ล้วน", g_list12("name")),
 ],
 "B": [
  ("short_num", "short_answer", "maker", "คำนวณ 19*29 ตอบเฉพาะตัวเลขอย่างเดียว", g_digits("551")),
  ("short_date", "short_answer", "maker", "วันที่ 2026-10-31 บวก 60 วัน คือวันที่เท่าไร ตอบรูปแบบ YYYY-MM-DD อย่างเดียว", g_exact("2026-12-30")),
  ("extract", "analysis", "analyst", "ดึงข้อมูลจากข้อความนี้ ตอบ JSON ล้วนคีย์ invoice, customer, amount (ตัวเลข), due (YYYY-MM-DD ค.ศ.):\nใบแจ้งหนี้เลขที่ INV-0107 ลูกค้า ร้านสยามค้าไม้ ยอด 3,000 บาท ครบกำหนด 1 ม.ค. 2570", g_extract({"invoice": "INV-0107", "customer": "ร้านสยามค้าไม้", "amount": 3000.0, "due": "2027-01-01"})),
  ("lines3", "instruction", "maker", "เขียนข้อดีของการออกใบแจ้งหนี้ที่ชัดเจน 3 ข้อ เป็นรายการ 3 บรรทัดพอดี ขึ้นต้นทุกบรรทัดด้วย '- ' ห้ามมีข้อความอื่น ห้ามใช้อักษรจีน", g_lines3),
  ("en_one", "instruction", "maker", "Answer in one English sentence under 25 words, no Thai characters: what is a purchase order?", g_en_one),
  ("code", "code", "maker", "เขียนฟังก์ชัน Python `def late_fee(amount: float, days_late: int) -> float` คืน 0.0 ถ้า days_late<=0 ไม่เช่นนั้นคืน round(amount*0.015*days_late/30, 2) ตอบเป็นโค้ดล้วน",
   g_code("late_fee", [("(10000,30)", 150.0), ("(10000,0)", 0.0), ("(10000,-5)", 0.0), ("(2500,45)", 56.25)])),
  ("rev_bad", "review", "reviewer", REV("คำนวณ 19*29 ตอบเฉพาะตัวเลขอย่างเดียว", "541"), g_verdict(False)),
  ("rev_good", "review", "reviewer", REV("ตอบ 1 ประโยคภาษาไทย: ข้อดีของใบแจ้งหนี้ที่ชัดเจน", "ลดข้อโต้แย้งเรื่องยอดและช่วยให้ลูกค้าชำระเงินได้เร็วขึ้น"), g_verdict(True)),
  ("synth", "synthesis", "editor", SYN(["ใบแจ้งหนี้ครึ่งหนึ่งไม่ระบุเครดิตเทอมที่ตกลงกันไว้", "ลูกค้าสองรายโต้แย้งยอดเพราะเอกสารไม่ตรงกัน", "ทีมบัญชีใช้เวลาตามเอกสารมากกว่าตามเงิน"], "รวมเป็นสรุปเดียวไม่เกิน 150 ตัวอักษร ต้องมีคำว่า 'ใบแจ้งหนี้' 'เครดิตเทอม' 'ตามเงิน'"), g_summary(["ใบแจ้งหนี้", "เครดิตเทอม", "ตามเงิน"])),
  ("long12", "long_output", "maker", "สร้าง JSON array 12 รายการ แต่ละรายการมีคีย์ id (1 ถึง 12 เรียงลำดับ) และ name (ชื่อจังหวัดในประเทศไทย ภาษาไทย) ตอบ JSON ล้วน", g_list12("name")),
 ],
}

CORE = "ตอบตรงตามรูปแบบที่สั่งเท่านั้น: ไม่มีคำนำ ไม่มีคำลงท้าย ไม่อธิบายเพิ่มเมื่อไม่ได้ขอ"
RULES = {
 "fence_or_extra": "เมื่อสั่งให้ตอบ JSON ให้ตอบ JSON ล้วน ตัวอักษรแรกต้องเป็น { หรือ [ ห้ามมี ``` ห้ามมีข้อความก่อนหรือหลัง",
 "json_invalid": "ตรวจว่า JSON ถูกต้อง (ปิดวงเล็บครบ ใช้เครื่องหมายคำพูดคู่ ไม่มีคอมม่าเกิน) ก่อนส่ง",
 "cjk": "ห้ามใช้อักษรจีน ญี่ปุ่น เกาหลีเด็ดขาด ใช้ภาษาไทยหรืออังกฤษเท่านั้น",
 "empty": "ต้องตอบทุกครั้ง ห้ามส่งข้อความว่าง ถ้างานยาวให้ย่อแทนการไม่ตอบ",
 "format": "นับจำนวนบรรทัด/ข้อ/ตัวอักษรที่สั่งให้ตรงเป๊ะ และใช้ภาษาตามที่สั่ง",
 "extra_text": "เมื่อสั่งให้ตอบคำตอบเดียว ให้ตอบเฉพาะคำตอบนั้น ห้ามมีประโยคอธิบาย",
 "wrong_value": "คิดทีละขั้นให้ถูกก่อน (คำนวณตัวเลข แปลง พ.ศ.→ค.ศ. นับวันตามปฏิทินจริง) แล้วตอบเฉพาะผลสุดท้าย",
 "review_wrong": "ผู้ตรวจ: เทียบงานกับโจทย์ทีละข้อ (ตัวเลขถูกไหม ภาษาถูกไหม ความยาว/จำนวนข้อตรงไหม) ให้ pass=false เมื่อพบข้อผิดจริงเท่านั้น และ pass=true เมื่อถูกครบ",
 "code_wrong": "เขียนโค้ดให้ครอบคลุมค่าขอบ (0, ค่าติดลบ, ค่าที่ขอบช่วง) ตามสเปกเป๊ะ ตอบเป็นโค้ดล้วน",
 "code_timeout": "โค้ดต้องไม่วนไม่รู้จบ และไม่รอรับ input",
 "missing_words": "เมื่อสั่งให้มีคำใด ต้องใส่คำนั้นตามตัวอักษรครบทุกคำ",
}


def now_ms():
    return int(time.time() * 1000)


def conn():
    c = sqlite3.connect(DB, timeout=60)
    c.execute("create table if not exists train_runs(ts real,phase text,setname text,seat text,model text,task text,skill text,rep int,ok int,err int,tag text,ms int,reply text)")
    c.execute("create table if not exists seat_coach(seat text primary key,text text,model text,updated real,applied int default 0,note text)")
    return c


def pace(c):
    while c.execute("select count(*) from audit where t>? and blocked is null", (time.time() - 3600,)).fetchone()[0] >= PER_HOUR_CAP:
        time.sleep(60)


def run_item(c, seat, model, phase, sname, item, rep, coach):
    tid, skill, brole, prompt, grader = item
    done = c.execute("select 1 from train_runs where phase=? and seat=? and task=? and rep=? and setname=? and model=?", (phase, seat, tid, rep, sname, model)).fetchone()
    if done:
        return
    base = c.execute("select prompt from roles where name=?", (brole,)).fetchone()[0]
    role = base + (("\n\n" + coach) if coach else "")
    gw = c.execute("select k.gateway from seats s join catalog k on k.name=s.catalog where s.name=?", (seat,)).fetchone()[0]
    text, ok, t0 = "", False, time.time()
    with GW_SEM.setdefault(gw, threading.Semaphore(2)):
        for attempt in range(2):
            pace(c)
            t0 = time.time()
            ok, text = team.call(c, seat, prompt, job="train", role_ov=role)
            if ok:
                break
            time.sleep(8)
    ms = int((time.time() - t0) * 1000)
    if not ok:
        c.execute("insert into train_runs values(?,?,?,?,?,?,?,?,?,?,?,?,?)", (time.time(), phase, sname, seat, model, tid, skill, rep, 0, 1, "infra", ms, text[:300])); c.commit(); return
    good, tag = grader(text)
    c.execute("insert into train_runs values(?,?,?,?,?,?,?,?,?,?,?,?,?)", (time.time(), phase, sname, seat, model, tid, skill, rep, int(good), 0, tag, ms, clean(text)[:300])); c.commit()


def build_coach(c, seat, model):
    tags = [r[0] for r in c.execute("select distinct tag from train_runs where phase='A_base' and seat=? and model=? and ok=0 and err=0 and tag is not null", (seat, model))]
    lines = [CORE] + [RULES[t] for t in tags if t in RULES]
    text = "คำแนะนำการทำงาน (เรียนรู้จากข้อผิดพลาดที่ผ่านมาของคุณ):\n" + "\n".join(f"- {x}" for x in lines)
    c.execute("insert into seat_coach(seat,text,model,updated,applied,note) values(?,?,?,?,0,?) on conflict(seat) do update set text=?,model=?,updated=?,note=?",
              (seat, text, model, time.time(), "จากข้อผิดพลาด: " + (", ".join(tags) or "ไม่มี"), text, model, time.time(), "จากข้อผิดพลาด: " + (", ".join(tags) or "ไม่มี")))
    c.commit()
    return text


def worker(seat, repeats):
    c = conn()
    model = team.resolve(c, seat)[4] if False else c.execute("select catalog from seats where name=?", (seat,)).fetchone()[0]
    for it in SETS["A"]:
        run_item(c, seat, model, "A_base", "A", it, 0, None)
    coach = build_coach(c, seat, model)
    for rep in range(repeats):
        for it in SETS["B"]:
            run_item(c, seat, model, "B_base", "B", it, rep, None)
            run_item(c, seat, model, "B_coached", "B", it, rep, coach)
    print("เสร็จ", seat, flush=True)


def cmd_run(repeats):
    c = conn()
    seats = [r[0] for r in c.execute("select name from seats where catalog is not null order by rowid")]
    c.close()
    with ThreadPoolExecutor(len(seats)) as ex:
        list(ex.map(lambda s: worker(s, repeats), seats))


def stats(c, seat, model, phase):
    rows = c.execute("select ok,err,skill from train_runs where seat=? and model=? and phase=?", (seat, model, phase)).fetchall()
    valid = [r for r in rows if r[1] == 0]
    return (sum(r[0] for r in valid), len(valid), sum(r[1] for r in rows))



SKILL_TH = {"short_answer": "ตอบสั้น/คำนวณ", "analysis": "ดึงข้อมูลเป็น JSON", "instruction": "ทำตามรูปแบบที่สั่ง", "code": "เขียนโค้ดสั้น", "review": "ตรวจงาน (ตัดสิน pass/fail)", "synthesis": "รวม/สรุปงาน", "long_output": "ผลลัพธ์ JSON ยาว"}
TAG_TH = {"fence_or_extra": "JSON มี ``` หรือข้อความแปลกปน", "json_invalid": "JSON พัง", "cjk": "อักษรจีน/ญี่ปุ่นปน", "empty": "ตอบว่าง", "format": "ไม่ตรงรูปแบบ/ความยาวที่สั่ง", "extra_text": "ตอบเกิน (มีคำอธิบายเพิ่ม)", "wrong_value": "คำตอบผิด", "review_wrong": "ตัดสินผิด", "code_wrong": "โค้ดไม่ผ่านเทส", "code_timeout": "โค้ดค้าง", "missing_words": "ขาดคำที่สั่งให้ใส่"}


def _rate(rows):
    v = [r for r in rows if r["err"] == 0]
    return sum(r["ok"] for r in v), len(v), sum(r["err"] for r in rows)


def load(c):
    c.row_factory = sqlite3.Row
    return c.execute("select * from train_runs where setname='B'").fetchall()


def verdict(b, a):
    d = a[0] - b[0]
    if a[1] < 10 or b[1] < 10:
        return "ข้อมูลไม่พอ"
    if d >= 3:
        return "ดีขึ้นชัด"
    if d <= -3:
        return "แย่ลง"
    return "ทรงตัว (ต่างกันอยู่ในระดับสัญญาณรบกวน)"


def cmd_report(out):
    c = conn()
    rows = load(c)
    c2 = conn(); c2.row_factory = sqlite3.Row
    seats = c2.execute("select s.name,s.title,s.catalog,k.model,k.gateway from seats s join catalog k on k.name=s.catalog order by s.rowid").fetchall()
    L = ["# รายงาน Performance ทีม AI (ผลเทรน)", "", f"วันที่วัด: {time.strftime('%Y-%m-%d', time.localtime(max([r['ts'] for r in rows] or [time.time()])))}", "",
         "## 'เทรน' ในที่นี้คืออะไร", "",
         "โมเดลเป็นบริการโฮสต์ ไม่มีการ fine-tune น้ำหนักโมเดล สิ่งที่ทำคือ (1) เขียน **คู่มือสั่งงาน (coaching)** ต่อสมาชิกจากข้อผิดพลาดจริงในชุดทดสอบ A แล้ว (2) วัดผลบนชุดทดสอบ B ซึ่งเป็นคนละโจทย์ (ตัวเลข/ข้อความต่างกัน) ทั้งก่อนและหลังใส่คู่มือ โดยใช้ role prompt ตัวเดียวกันทั้งสองรอบ ต่างกันแค่คู่มือที่เพิ่มต่อท้าย",
         "ชุดทดสอบ B มี 10 โจทย์ x ทำซ้ำ 2 รอบ = 20 ครั้งต่อรอบ ตรวจอัตโนมัติด้วยโปรแกรม (ไม่ใช้ LLM ตัดสิน) ครอบคลุม: ตอบสั้น/คำนวณ, ดึงข้อมูล JSON, ทำตามรูปแบบ, โค้ด, ตรวจงาน, รวมงาน, JSON ยาว",
         "**ข้อควรระวัง:** 20 ครั้งต่อรอบน้อย ความต่างน้อยกว่า 3 ครั้งถือเป็นแค่ทิศทาง ไม่ใช่ข้อสรุป ครั้งที่ติดปัญหาโครงสร้างพื้นฐาน (HTTP error/timeout/ถูกบล็อก) แยกนับและไม่เอามาคิดคะแนน", ""]
    L += ["## สรุปรายคน", "", "| สมาชิก | โมเดล | ก่อน (ผ่าน/ครั้ง) | หลัง (ผ่าน/ครั้ง) | ผลต่าง | ข้อผิดพลาดระบบ | สรุป |", "|---|---|---|---|---|---|---|"]
    per = {}
    for s in seats:
        r = [x for x in rows if x["seat"] == s["name"] and x["model"] == s["catalog"]]
        b = _rate([x for x in r if x["phase"] == "B_base"]); a = _rate([x for x in r if x["phase"] == "B_coached"])
        per[s["name"]] = (b, a)
        L.append(f"| {s['name']} ({s['title']}) | {s['model']} | {b[0]}/{b[1]} | {a[0]}/{a[1]} | {a[0]-b[0]:+d} | {b[2]+a[2]} | {verdict(b, a)} |")
    L += ["", "## แยกตามทักษะ (ผ่าน/ครั้ง ก่อน → หลัง)", ""]
    sk = sorted({x["skill"] for x in rows})
    L += ["| สมาชิก | " + " | ".join(SKILL_TH.get(k, k) for k in sk) + " |", "|---|" + "---|" * len(sk)]
    for s in seats:
        cells = []
        for k in sk:
            r = [x for x in rows if x["seat"] == s["name"] and x["model"] == s["catalog"] and x["skill"] == k]
            b = _rate([x for x in r if x["phase"] == "B_base"]); a = _rate([x for x in r if x["phase"] == "B_coached"])
            cells.append(f"{b[0]}/{b[1]} → {a[0]}/{a[1]}")
        L.append(f"| {s['name']} | " + " | ".join(cells) + " |")
    L += ["", "## จุดที่ยังพลาดหลังใส่คู่มือ", ""]
    for s in seats:
        r = [x for x in rows if x["seat"] == s["name"] and x["model"] == s["catalog"] and x["phase"] == "B_coached" and x["err"] == 0 and not x["ok"]]
        tags = {}
        for x in r:
            tags[x["tag"]] = tags.get(x["tag"], 0) + 1
        L.append(f"- **{s['name']}**: " + (", ".join(f"{TAG_TH.get(t, t)} x{n}" for t, n in sorted(tags.items(), key=lambda z: -z[1])) or "ไม่มีข้อผิดพลาดที่ตรวจพบ"))
    L += ["", "## ข้อจำกัดของชุดทดสอบ (อ่านก่อนตีความ)", "",
          "- **เพดานคะแนน:** โจทย์ชุดนี้ค่อนข้างง่าย หลายคนผ่านเกือบเต็มตั้งแต่ก่อนใส่คู่มือ ผลต่างจึงเล็ก ไม่ได้แปลว่าคู่มือไม่มีประโยชน์เสมอไป แต่แปลว่าชุดนี้แยกความต่างได้น้อย รอบหน้าควรเพิ่มโจทย์ยาก (หลายขั้น เอกสารยาว ภาษาไทยปนตัวเลข) และเพิ่มจำนวนซ้ำ",
          "- **ตัวอย่างน้อย:** แต่ละทักษะมี 2-4 ครั้งต่อรอบ ตัวเลขรายทักษะเป็นแค่เบาะแส เช่น seat4 ดึง JSON ได้ 2/2 → 0/2 อาจเป็นความผันผวนหรือคู่มือรบกวนก็ได้ ต้องวัดซ้ำก่อนสรุป",
          "- **ชุด A/B คนละโจทย์ แต่ชนิดเดียวกัน:** ลดการสอนข้อสอบได้ แต่ยังไม่ใช่งานจริงของคุณ ควรเพิ่มโจทย์จากงานจริง (ที่ไม่มีข้อมูลลูกค้า) เข้าชุดทดสอบ",
          "- **ไม่ได้วัดคุณภาพเชิงเนื้อหา** (ความคิดสร้างสรรค์ ความลึก ความถูกต้องของข้อเท็จจริงยาวๆ) วัดเฉพาะสิ่งที่ตรวจด้วยโปรแกรมได้",
          "- **ผลต่อการจัดงาน:** คะแนนทักษะจริง (seat_skills) ไม่ถูกแก้ ใช้รายงานนี้เป็นหลักฐานประกอบตัดสินใจเท่านั้น", ""]
    L += ["", "## การนำไปใช้ในระบบ", ""]
    for s in seats:
        co = c2.execute("select applied from seat_coach where seat=?", (s["name"],)).fetchone()
        b, a = per[s["name"]]
        L.append(f"- {s['name']}: " + ("เปิดใช้คู่มือในการเรียกงานจริงแล้ว" if co and co[0] else "ยังไม่เปิดใช้คู่มือ (ผลไม่ดีขึ้นหรือข้อมูลไม่พอ)"))
    L += ["", "คะแนนทักษะที่ใช้จัดงาน (seat_skills) ไม่ถูกแก้โดยการเทรนนี้ ยังคงมาจากงานจริงสะสมเท่านั้น", "", f"_สร้างโดย `python3 train.py report`_"]
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("เขียน", out)


def cmd_apply():
    """เปิดใช้คู่มือเฉพาะสมาชิกที่ผลหลัง >= ก่อน (บนชุด B) และเปิดแฟล็ก coach_enabled"""
    c = conn(); c.row_factory = sqlite3.Row
    rows = c.execute("select * from train_runs where setname='B'").fetchall()
    for s in c.execute("select name,catalog from seats where catalog is not null").fetchall():
        r = [x for x in rows if x["seat"] == s["name"] and x["model"] == s["catalog"]]
        b = _rate([x for x in r if x["phase"] == "B_base"]); a = _rate([x for x in r if x["phase"] == "B_coached"])
        ok = 1 if (a[1] >= 10 and a[0] >= b[0]) else 0
        c.execute("update seat_coach set applied=? where seat=?", (ok, s["name"]))
        print(s["name"], b, a, "เปิดใช้" if ok else "ไม่เปิด")
    c.execute("insert or replace into settings values('coach_enabled','1')"); c.commit()



EXAMPLES = {
 "short_answer": ("คำนวณ 19*29 ตอบเฉพาะตัวเลขอย่างเดียว", "551"),
 "analysis": ("ดึงข้อมูลจากข้อความนี้ ตอบ JSON ล้วนคีย์ invoice, customer, amount, due (YYYY-MM-DD ค.ศ.): <ข้อความ>", '{"invoice":"INV-0107","customer":"ร้านสยามค้าไม้","amount":3000,"due":"2027-01-01"}'),
 "instruction": ("เขียนข้อดี 3 ข้อ เป็นรายการ 3 บรรทัดพอดี ขึ้นต้นด้วย '- ' ห้ามมีข้อความอื่น", "- ข้อ 1\n- ข้อ 2\n- ข้อ 3"),
 "code": ("เขียนฟังก์ชัน Python ... ตอบเป็นโค้ดล้วน", "def late_fee(amount, days_late): ..."),
 "review": ("โจทย์ที่ผู้ทำได้รับ: ...\nงานที่ผู้ทำส่งมา: ...\n\nตัดสินว่าผ่านหรือไม่ ตอบ JSON ล้วน", '{"pass": false, "reasons": ["ตัวเลขผิด"]}'),
 "synthesis": ("งานย่อย 1) ... 2) ... 3) ...\n\nรวมเป็นสรุปเดียวไม่เกิน 150 ตัวอักษร ต้องมีคำว่า ... (วางคำสั่งไว้ท้ายสุดเสมอ)", "สรุปหนึ่งย่อหน้า"),
 "long_output": ("สร้าง JSON array 12 รายการ คีย์ id, name ตอบ JSON ล้วน", '[{"id":1,"name":"..."}, ...]'),
}
ENV_NAME = lambda gw: "AI_TEAM_KEY_" + re.sub(r"[^A-Z0-9]", "_", gw.upper())


def cmd_guide(outdir):
    """สร้างคู่มือสั่งงานรายคน (md) จาก team.db — อ่านได้บนเครื่องที่ไม่มี team.db ไม่มี key"""
    os.makedirs(outdir, exist_ok=True)
    c = conn(); c.row_factory = sqlite3.Row
    seats = c.execute("select s.name,s.title,s.role,s.catalog,k.model,k.gateway,g.base_url from seats s join catalog k on k.name=s.catalog join gateways g on g.name=k.gateway order by s.rowid").fetchall()
    B = c.execute("select * from train_runs where setname='B'").fetchall()
    idx = ["# คู่มือสั่งงานทีม AI (Power AI Ranger)", "",
           "ไฟล์นี้และไฟล์รายคนในโฟลเดอร์เดียวกันสร้างจากฐานข้อมูลของทีมด้วย `python3 scripts/train.py guide docs/team` สร้างใหม่ได้หลังเทรนซ้ำ ไม่มี API key อยู่ในไฟล์ใดๆ", "",
           "## ใช้บนเครื่อง/เซสชันอื่น", "",
           "1. clone repo นี้ แล้วอ่านไฟล์ของสมาชิกที่ต้องการ (`docs/team/<ชื่อ>.md`)",
           "2. ตั้ง key ใน environment ตามชื่อตัวแปรที่ระบุในไฟล์ (ห้ามใส่ key ลงไฟล์หรือ repo)",
           "3. เรียกผ่าน curl/Python ตามตัวอย่างในไฟล์ หรือใช้ `scripts/team.py` ถ้าตั้ง gateway/seat ไว้แล้ว",
           "4. เซสชัน Claude ใหม่: ให้อ่าน `docs/TEAM-GUIDE.md` กับไฟล์รายคนก่อนสั่งงาน แล้วใช้ system prompt + คู่มือ (coaching) ตามที่ระบุตัวต่อตัว", "",
           "## สมาชิก", "", "| ชื่อ | บทบาท | โมเดล | ถนัด | ผลเทรน (ก่อน→หลัง) |", "|---|---|---|---|---|"]
    for s in seats:
        r = [x for x in B if x["seat"] == s["name"] and x["model"] == s["catalog"]]
        b = _rate([x for x in r if x["phase"] == "B_base"]); a = _rate([x for x in r if x["phase"] == "B_coached"])
        bysk = {}
        for k in {x["skill"] for x in r}:
            rr = [x for x in r if x["skill"] == k and x["phase"] == "B_coached" and x["err"] == 0]
            if rr:
                bysk[k] = sum(x["ok"] for x in rr) / len(rr)
        good = [SKILL_TH.get(k, k) for k, v in sorted(bysk.items(), key=lambda z: -z[1]) if v >= 0.8]
        bad = [SKILL_TH.get(k, k) for k, v in sorted(bysk.items(), key=lambda z: z[1]) if v < 0.5]
        co = c.execute("select text,applied,note from seat_coach where seat=?", (s["name"],)).fetchone()
        tagrows = {}
        for x in r:
            if x["phase"] == "B_coached" and not x["ok"] and not x["err"]:
                tagrows[x["tag"]] = tagrows.get(x["tag"], 0) + 1
        sk_db = c.execute("select skill,score,n from seat_skills where seat=? order by score desc", (s["name"],)).fetchall()
        env = ENV_NAME(s["gateway"])
        base = s["base_url"]
        L = [f"# {s['name']} — {s['title']}", "",
             f"- โมเดล: `{s['model']}`", f"- Gateway: `{s['gateway']}` — base URL `{base}`", f"- ตัวแปร key (ตั้งเองในเครื่อง ห้ามใส่ในไฟล์): `{env}`",
             f"- ผลเทรน (ชุดทดสอบ B): ก่อนใส่คู่มือ {b[0]}/{b[1]} → หลัง {a[0]}/{a[1]} (ข้อผิดพลาดระบบ {b[2]+a[2]} ครั้ง ไม่นับคะแนน) — ดูรายงานฉบับเต็มใน docs/performance-*.md", ""]
        L += ["## ใช้กับงานแบบไหน", ""] + ([f"- {x}" for x in good] or ["- (ยังไม่มีทักษะที่ผ่านเกณฑ์ 80% ในชุดทดสอบ)"]) + ["", "## อย่าใช้กับงานแบบไหน", ""] + ([f"- {x}" for x in bad] or ["- (ไม่พบทักษะที่ต่ำกว่า 50%)"]) + [""]
        if sk_db:
            L += ["คะแนนทักษะจากงานจริงสะสม (ใช้จัดงานอัตโนมัติ): " + ", ".join(f"{x['skill']} {x['score']:.2f} (n={x['n']})" for x in sk_db), ""]
        L += ["## System prompt (ใช้ตามนี้ทุกคำ)", "", "บทบาทประจำตำแหน่ง:", "", "```", s["role"] or "", "```", ""]
        if co:
            L += [f"คู่มือสั่งงานที่ได้จากการเทรน ({'เปิดใช้ในระบบแล้ว' if co['applied'] else 'ยังไม่เปิดใช้ในระบบ'}) — ต่อท้าย system prompt:", "", "```", co["text"], "```", "", f"ที่มา: {co['note']}", ""]
        L += ["## ตัวอย่างการสั่งงาน", ""]
        for k in (good[:2] and [kk for kk, vv in sorted(bysk.items(), key=lambda z: -z[1]) if vv >= 0.8][:2]) or [kk for kk, vv in sorted(bysk.items(), key=lambda z: -z[1])][:1]:
            ex = EXAMPLES.get(k)
            if ex:
                L += [f"**{SKILL_TH.get(k, k)}**", "", "คำสั่ง:", "", "```", ex[0], "```", "", "รูปแบบผลลัพธ์ที่คาดหวัง:", "", "```", ex[1], "```", ""]
        L += ["## ข้อผิดพลาดที่ยังพบ (หลังใส่คู่มือ)", ""] + ([f"- {TAG_TH.get(t, t)} x{n}" for t, n in sorted(tagrows.items(), key=lambda z: -z[1])] or ["- ไม่พบในชุดทดสอบ"]) + [""]
        L += ["ข้อควรรู้ทั่วไป: ตัดข้อความระหว่าง `<think>...</think>` ออกก่อนใช้ผลลัพธ์ และตรวจรูปแบบ (JSON) ด้วยโปรแกรมทุกครั้ง อย่าเชื่อว่าโมเดลทำตามรูปแบบเสมอ", ""]
        L += ["## เรียกใช้ทันที", "", "```bash", f"export {env}=...   # ใส่ key ของคุณเอง", f"curl -s {base.rstrip('/')}/chat/completions \\", f"  -H \"Authorization: Bearer ${env}\" -H 'Content-Type: application/json' \\", f"  -d '{{\"model\": \"{s['model']}\", \"messages\": [{{\"role\":\"system\",\"content\":\"<system prompt จากด้านบน>\"}},{{\"role\":\"user\",\"content\":\"<คำสั่ง>\"}}]}}'", "```", "",
              "```python", "import os, json, urllib.request", f"req = urllib.request.Request('{base.rstrip('/')}/chat/completions',", f"    json.dumps({{'model': '{s['model']}', 'messages': [{{'role': 'system', 'content': SYSTEM}}, {{'role': 'user', 'content': BRIEF}}]}}).encode(),", f"    {{'Authorization': 'Bearer ' + os.environ['{env}'], 'Content-Type': 'application/json'}})", "print(json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content'])", "```", ""]
        open(os.path.join(outdir, s["name"] + ".md"), "w", encoding="utf-8").write("\n".join(L))
        idx.append(f"| [{s['name']}]({s['name']}.md) | {s['title']} | `{s['model']}` | {', '.join(good[:3]) or '-'} | {b[0]}/{b[1]} → {a[0]}/{a[1]} |")
    open(os.path.join(os.path.dirname(outdir.rstrip('/')) or '.', "TEAM-GUIDE.md"), "w", encoding="utf-8").write("\n".join(idx).replace("](", "](team/") .replace("(team/http", "(http") + "\n")
    print("สร้างคู่มือ", len(seats), "คน ที่", outdir)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        reps = int(sys.argv[sys.argv.index("--repeats") + 1]) if "--repeats" in sys.argv else 2
        cmd_run(reps)
    elif len(sys.argv) > 2 and sys.argv[1] == "report":
        cmd_report(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "guide":
        cmd_guide(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "apply":
        cmd_apply()
    else:
        print(__doc__)
