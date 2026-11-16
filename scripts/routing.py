# -*- coding: utf-8 -*-
"""Dynamic Model Routing + Cost Guardrails ของ AI Team (stdlib ล้วน, Python 3.9)

ส่วนที่ 1 — Guardrails (ทุกการเรียกโมเดลผ่าน team.call จุดเดียว):
  reserve()  จองงบก่อนเรียก ใน transaction แบบ BEGIN IMMEDIATE (worker หลายเธรด/หลายโปรเซสจองพร้อมกันแล้วไม่ทะลุเพดาน)
  settle()   ปิดยอดด้วย usage จริงจากผู้ให้บริการ (ถ้าไม่มี ใช้ค่าประมาณและติดป้าย src='est')
  release()  คืนงบเมื่อเรียกล้มเหลว
  เพดาน: โทเคนและ USD ต่องาน(job)/วัน/เดือน, เตือนที่ warn_pct, ราคาไม่ทราบ "ไม่ใช่ฟรี": เมื่อมีเพดาน USD และโมเดลไม่มีราคา
  ค่าเริ่มต้นคือข้ามช่องนั้น (ตั้งราคา 0 ให้โมเดลฟรีด้วย `catalog price NAME --in-cost 0 --out-cost 0`)
ส่วนที่ 2 — Routing:
  classify()   ประเมินระดับงาน light/standard/heavy จากข้อความ งานที่ตรวจได้อัตโนมัติ และความถนัด
  rank()       เลือกช่องที่ "ถูกที่สุดที่ฝีมือถึงเกณฑ์" (heavy: ฝีมือก่อน) จากคะแนนที่วัดจริง (seat_skills/train_levels)
  healthy()    ข้ามช่องที่เพิ่งล้มด้วย 402/quota/429 จนกว่าจะพ้นช่วงพัก (อ่านจากตาราง audit ไม่เก็บสถานะเพิ่ม)
  next_tier()  ถ้างานตรวจไม่ผ่านที่ระดับต่ำ ยกระดับขึ้นหนึ่งขั้น (ตามจำนวนรอบที่ตั้งไว้) ผ่านเพดานงบเดิมเสมอ
"""
import math, re, threading, time

TIERS = ["light", "standard", "heavy"]
BAR = {"light": 0.3, "standard": 0.5, "heavy": 0.7}  # คะแนนความถนัดขั้นต่ำของแต่ละระดับ
DEFAULTS = {
    "guard:enabled": "1", "guard:warn_pct": "80", "guard:reserve_out": "2000", "guard:max_tokens": "0",
    "guard:unpriced": "block",  # block = ข้ามช่องที่ไม่มีราคาเมื่อมีเพดาน USD | allow = ให้เรียกได้ (ยังนับโทเคน)
    "cap:job_tokens": "off", "cap:day_tokens": "2000000", "cap:month_tokens": "40000000",
    "route:enabled": "1", "route:escalate": "1", "route:max_escalations": "1",
    # decide(): โมเดล Claude ต่อเลน (ค่าที่ Agent tool รับ: haiku|sonnet|opus|fable) · top = ขั้นสูงสุดเมื่อ opus ยังล้ม (ว่าง = ถามผู้ใช้)
    "exec:action": "sonnet", "exec:general": "sonnet", "exec:judgment": "opus", "exec:bulk": "haiku", "exec:top": "",
    "exec:free_pool": "@auto", "route:opus_share": "0.3",
    # route:mode — free-first (ค่าเริ่มต้น) = ทีมฟรีทำทุกอย่างที่ไม่ต้องใช้เครื่องมือและไม่เสี่ยงสูง Claude เฉพาะลงมือกด/เสี่ยงสูง/ทีมฟรีล้มซ้ำ | balanced = เลน judgment ไป Opus เสมอ
    # route:learn_min/free_min_pass — เมื่อเลนหนึ่งมีผลจริงของทีมฟรี >= learn_min งาน และผ่านต่ำกว่า free_min_pass จะข้ามไป Claude ตรง (ไม่เสียรอบฟรีที่ล้มแน่)
    "route:mode": "free-first", "route:learn_min": "5", "route:free_min_pass": "0.6", "route:escalate_after": "2", "cap:claude_day_tokens": "off",
}
KEYS = {  # ชื่อที่ผู้ใช้พิมพ์ -> คีย์ใน settings
    "day-tokens": "cap:day_tokens", "month-tokens": "cap:month_tokens", "job-tokens": "cap:job_tokens",
    "day-usd": "cap:day_usd", "month-usd": "cap:month_usd", "job-usd": "cap:job_usd",
    "claude-day-tokens": "cap:claude_day_tokens", "warn": "guard:warn_pct", "unpriced": "guard:unpriced", "reserve-out": "guard:reserve_out", "max-tokens": "guard:max_tokens",
    "enabled": "guard:enabled", "route": "route:enabled", "escalate": "route:escalate", "max-escalations": "route:max_escalations",
}
LIVE = ("reserved", "settled", "expired")  # สถานะที่นับเป็นยอดใช้ (failed = คืนงบแล้ว)
STALE_S = 1200  # การจองค้างเกิน 20 นาที (โปรเซสตาย) ติดป้าย expired แต่ยังนับตามค่าประมาณ (ระวังไว้ก่อน) ล้างด้วย `budget reap`

tl = threading.local()  # team.chat เก็บ usage ของการเรียกล่าสุดของเธรดนี้ไว้ที่นี่
_warned = set()


# ---------- settings / schema ----------
def get(c, k):
    r = c.execute("select v from settings where k=?", (k,)).fetchone()
    return r[0] if r else DEFAULTS.get(k)


def num(v):
    """'off'/''/None = ไม่จำกัด"""
    if v is None or str(v).strip().lower() in ("", "off", "none", "unlimited"):
        return None
    return float(v)


def ensure(c):
    c.execute("""create table if not exists spend(id integer primary key autoincrement, t real, seat text, model text, job text, audit_id integer,
        state text, est_in integer, est_out integer, est_cost real, in_tok integer, out_tok integer, cost real, src text)""")
    have_s = [r[1] for r in c.execute("pragma table_info(spend)")]
    for col, typ in (("family", "text default 'team'"), ("reason_tok", "integer"), ("usage_raw", "text"), ("task_id", "integer"), ("tier", "text"), ("lane", "text"), ("xparam", "text")):
        if col not in have_s:
            c.execute(f"alter table spend add column {col} {typ}")  # family: team = ทีม ai-team (นับเพดาน) | claude = Claude subagent (บันทึกด้วย claude-log)
    # พารามิเตอร์พิเศษต่อโมเดล (เช่น reasoning_effort) เก็บแยกตาราง: catalog ถูก insert-or-replace ในหลายจุดจะทำให้ค่าหาย
    c.execute("create table if not exists catalog_extra(name text primary key, body text not null, tiers text not null default 'light,standard')")
    c.execute("create table if not exists shadow_results(id integer primary key autoincrement, t real, set_name text, side text, correct integer, total integer, answered integer)")
    c.execute("create index if not exists spend_t on spend(t)")
    c.execute("create index if not exists spend_job on spend(job)")
    c.execute("""create table if not exists decisions(id integer primary key autoincrement, t real, job text, lane text, tier text, executor text,
        stakes integer, needs_tools integer, reason text, outcome text)""")
    have = [r[1] for r in c.execute("pragma table_info(tasks)")]
    for col, typ in (("tier", "text"), ("route_note", "text"), ("escalated", "integer default 0")):
        if col not in have:
            c.execute(f"alter table tasks add column {col} {typ}")
    c.commit()


def _start(kind, now):
    lt = time.localtime(now)
    if kind == "day":
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def used(c, since=None, job=None, family="team"):
    q = ("select coalesce(sum(coalesce(in_tok,est_in)+coalesce(out_tok,est_out)),0), coalesce(sum(coalesce(cost,est_cost)),0.0), "
         "coalesce(sum(case when cost is null and est_cost is null then coalesce(in_tok,est_in)+coalesce(out_tok,est_out) else 0 end),0) "
         "from spend where state in ('reserved','settled','expired') and coalesce(family,'team')=?")
    a = [family]
    if since is not None:
        q += " and t>=?"; a.append(since)
    if job is not None:
        q += " and job=?"; a.append(job)
    return c.execute(q, a).fetchone()  # (tokens, usd, unpriced_tokens)


def caps(c, scope, job=None):
    """(เพดานโทเคน, เพดาน USD) ของ scope = job|day|month — job ใช้ค่าเฉพาะงานก่อน ถ้าไม่มีใช้ค่ากลาง"""
    if scope == "job":
        t = c.execute("select v from settings where k=?", (f"jobcap:{job}:tokens",)).fetchone()
        u = c.execute("select v from settings where k=?", (f"jobcap:{job}:usd",)).fetchone()
        return num(t[0] if t else get(c, "cap:job_tokens")), num(u[0] if u else get(c, "cap:job_usd"))
    return num(get(c, f"cap:{scope}_tokens")), num(get(c, f"cap:{scope}_usd"))


# ---------- guardrails ----------
def estimate(role, brief, cin, cout, out_tokens, cli=False):
    est_in = int(math.ceil((len(role or "") + len(brief or "")) / 3.0))
    if cli:
        return est_in, out_tokens, 0.0  # CLI ใช้โควตาแพ็กเกจ ไม่มีราคาต่อโทเคน นับโทเคนอย่างเดียว
    cost = None if cin is None or cout is None else (est_in * cin + out_tokens * cout) / 1e6
    return est_in, out_tokens, cost


def reserve(c, seat, model, cin, cout, role, brief, job, cli=False):
    """จองงบก่อนเรียก คืน (kind, message, spend_id)  kind: ok | block (ทุกช่องก็ติดเหมือนกัน) | skip (เฉพาะช่องนี้)"""
    if get(c, "guard:enabled") != "1":
        return "ok", "", None
    out_t = int(get(c, "guard:max_tokens") or 0) or int(get(c, "guard:reserve_out") or 2000)
    est_in, est_out, est_cost = estimate(role, brief, cin, cout, out_t, cli)
    new_tok = est_in + est_out
    if c.in_transaction:
        c.commit()
    c.execute("begin immediate")  # ล็อกเขียนก่อนอ่านยอด: ตรวจ+จองเป็นก้อนเดียว ไม่มีช่องให้ใครแทรก
    try:
        now = time.time()
        c.execute("update spend set state='expired' where state='reserved' and t<?", (now - STALE_S,))
        scopes = [("month", _start("month", now), None, "รายเดือน"), ("day", _start("day", now), None, "รายวัน")]
        if job:
            scopes.insert(0, ("job", None, job, f"งาน '{job}'"))
        policy = get(c, "guard:unpriced")
        warn = ""
        for scope, since, jb, label in scopes:
            cap_t, cap_u = caps(c, scope, jb)
            if cap_t is None and cap_u is None:
                continue
            tok, usd, _ = used(c, since, jb)
            if cap_t is not None:
                if tok + new_tok > cap_t:
                    c.rollback()
                    return "block", f"งบโทเคน{label}เต็ม (ใช้ {tok:,.0f} + ขอ {new_tok:,} > เพดาน {cap_t:,.0f}) — ขอผู้ใช้อนุมัติก่อนเพิ่มเพดาน", None
                pct = (tok + new_tok) * 100.0 / cap_t
                if pct >= float(get(c, "guard:warn_pct") or 80):
                    warn = warn or f"งบโทเคน{label}ใช้ไป {pct:.0f}%"
            if cap_u is not None:
                if est_cost is None:
                    if policy == "block":
                        c.rollback()
                        return "skip", f"ช่อง {seat} ({model}) ไม่มีราคาใน catalog แต่มีเพดาน USD{label} — ตั้งราคาด้วย `catalog price` (โมเดลฟรีใส่ 0 0)", None
                elif usd + est_cost > cap_u:
                    c.rollback()
                    return "block", f"งบ USD{label}เต็ม (ใช้ ${usd:.4f} + ขอ ≈${est_cost:.4f} > เพดาน ${cap_u:.2f}) — ขอผู้ใช้อนุมัติก่อนเพิ่มเพดาน", None
                else:
                    pct = (usd + est_cost) * 100.0 / cap_u if cap_u else 100.0
                    if pct >= float(get(c, "guard:warn_pct") or 80):
                        warn = warn or f"งบ USD{label}ใช้ไป {pct:.0f}%"
        sid = c.execute("insert into spend(t,seat,model,job,state,est_in,est_out,est_cost,family,task_id,tier) values(?,?,?,?,'reserved',?,?,?,'team',?,?)",
                        (now, seat, model, job, est_in, est_out, est_cost, getattr(tl, "task", None), getattr(tl, "tier", None))).lastrowid
        c.commit()
    except Exception:
        if c.in_transaction:
            c.rollback()
        raise
    if warn and warn not in _warned:
        _warned.add(warn)
        import sys
        print(f"[WARN] {warn}", file=sys.stderr)
    return "ok", "", sid


def settle(c, sid, usage, reply, cin, cout, cli=False):
    if sid is None:
        return
    r = c.execute("select est_in,est_out from spend where id=?", (sid,)).fetchone()
    if not r:
        return
    ti = to = 0
    rt = None
    raw = None
    if isinstance(usage, dict):
        import json as _j
        raw = _j.dumps(usage, ensure_ascii=False)[:600]  # usage มีแต่ตัวเลขนับโทเคน ไม่มีเนื้อหา เก็บดิบไว้ตรวจว่าผู้ให้บริการส่งอะไรมา
        det = usage.get("completion_tokens_details")
        if isinstance(det, dict) and isinstance(det.get("reasoning_tokens"), int):
            rt = det["reasoning_tokens"]
        try:
            ti, to = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            ti = to = 0
    src = "usage"
    if ti + to == 0:
        ti, to, src = r[0], int(math.ceil(len(reply or "") / 3.0)), "est"
    cost = 0.0 if cli else (None if cin is None or cout is None else (ti * cin + to * cout) / 1e6)
    c.execute("update spend set state='settled',in_tok=?,out_tok=?,cost=?,src=?,reason_tok=?,usage_raw=? where id=?", (ti, to, cost, src, rt, raw, sid))
    c.commit()


def release(c, sid):
    if sid is not None:
        c.execute("update spend set state='failed' where id=?", (sid,))
        c.commit()


# ---------- health (circuit breaker จากตาราง audit) ----------
_RETRY = re.compile(r"(?i)retry[_ ]after\D{0,3}(\d+)")


def cooldown(blocked):
    """วินาทีที่ควรพักช่อง จากข้อความ error ล่าสุด (0 = ไม่ต้องพัก)"""
    m = _RETRY.search(blocked)
    ra = int(m.group(1)) if m else None
    if "HTTP 402" in blocked:
        return 3600
    if re.search(r"(?i)daily token quota|insufficient_quota|quota_exceeded", blocked):
        return min(ra or 3600, 21600)
    if "HTTP 429" in blocked:
        return min(ra or 30, 300)
    if "HTTP 401" in blocked or "HTTP 403" in blocked:
        return 1800
    return 0


def cooling(c, seat, now=None):
    r = c.execute("select t,blocked from audit where agent=? and (blocked is null or blocked like 'error:%') order by rowid desc limit 1", (seat,)).fetchone()
    if not r or not r[1]:
        return False
    return (now or time.time()) < r[0] + cooldown(r[1])


def healthy(c, seats):
    ok = [s for s in seats if not cooling(c, s)]
    return ok or list(seats)  # ถ้าพักหมดทุกช่อง ลองตามเดิมดีกว่าไม่ทำอะไร


# ---------- พารามิเตอร์ลดการคิดต่อโมเดล (ตั้งเองต่อโมเดลหลังวัดแล้วเท่านั้น: ผู้ให้บริการแต่ละเจ้าไม่เหมือนกัน) ----------
NO_OVERRIDE = {"model", "messages", "stream", "stream_options", "n"}


def extra_set(c, name, body, tiers="light,standard"):
    """ตั้ง JSON ที่จะผสมเข้า payload ของโมเดลนี้ ใช้เฉพาะรอบแรกของงานระดับ tiers (ไม่ใช้ตอนยกระดับ)"""
    import json
    if not c.execute("select 1 from catalog where name=?", (name,)).fetchone():
        raise ValueError(f"ไม่พบโมเดล '{name}' ในแคตตาล็อก")
    obj = json.loads(body) if isinstance(body, str) else body
    if not isinstance(obj, dict) or not obj:
        raise ValueError("extra ต้องเป็น JSON object ที่ไม่ว่าง เช่น {\"reasoning_effort\":\"low\"}")
    bad = NO_OVERRIDE & set(obj)
    if bad:
        raise ValueError("ห้ามทับคีย์: " + ", ".join(sorted(bad)))
    ts = [t.strip() for t in tiers.split(",") if t.strip()]
    if not ts or any(t not in TIERS for t in ts):
        raise ValueError("tiers ต้องเป็น light/standard/heavy คั่นด้วยจุลภาค")
    c.execute("insert or replace into catalog_extra(name,body,tiers) values(?,?,?)", (name, json.dumps(obj, ensure_ascii=False, sort_keys=True), ",".join(ts)))
    c.commit()


def extra_clear(c, name):
    c.execute("delete from catalog_extra where name=?", (name,)); c.commit()


def extra_for(c, catalog_name, tier, escalated=0):
    """dict ที่จะผสมเข้า payload: ว่างเสมอเมื่อ ไม่มีระดับงาน (เรียกตรงด้วย ask/run), ระดับไม่อยู่ใน tiers, หรือเป็นรอบยกระดับ (escalated>0)"""
    import json
    if tier not in TIERS or (escalated or 0) > 0:
        return {}
    r = c.execute("select body,tiers from catalog_extra where name=?", (catalog_name,)).fetchone()
    if not r or tier not in r[1].split(","):
        return {}
    try:
        d = json.loads(r[0])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


# ---------- routing ----------
HEAVY_WORDS = re.compile(r"(?i)architecture|refactor|security|วิเคราะห์เชิงลึก|ออกแบบระบบ|สถาปัตยกรรม|ความปลอดภัย|ข้อกฎหมาย|migrate|root cause")
HARD_SKILLS = {"code", "odoo_spec", "long_output", "test_design", "synthesis"}


def classify(brief, chk=None, skill=None, hint=None):
    """คืน (tier, เหตุผล) — hint = light|standard|heavy จากผู้สั่ง (ชนะทุกอย่าง)"""
    if hint in TIERS:
        return hint, "ผู้สั่งระบุ"
    pts, why = 0, []
    n = len(brief or "")
    if n > 6000:
        pts += 2; why.append("brief ยาวมาก")
    elif n > 2500:
        pts += 1; why.append("brief ยาว")
    elif n < 400:
        pts -= 1; why.append("brief สั้น")
    if chk and (chk.startswith("py:") or chk == "spec"):
        pts += 1; why.append("ต้องผ่านเทสที่รันได้")
    if skill in HARD_SKILLS:
        pts += 1; why.append(f"ทักษะ {skill} ยาก")
    elif skill == "short_answer":
        pts -= 1; why.append("คำตอบสั้น")
    if HEAVY_WORDS.search(brief or ""):
        pts += 1; why.append("มีคำชี้ว่าซับซ้อน")
    tier = "light" if pts <= -1 else ("heavy" if pts >= 3 else "standard")
    return tier, ", ".join(why) or "ค่ากลาง"


def unit_cost(c, seat):
    """ราคารวมต่อ 1M โทเคน (เข้า+ออก) สำหรับจัดอันดับ: 0 = ฟรี/CLI, None = ไม่ทราบราคา"""
    r = c.execute("select k.cin,k.cout,g.base_url from seats s join catalog k on k.name=s.catalog join gateways g on g.name=k.gateway where s.name=?", (seat,)).fetchone()
    if not r:
        return None
    # ช่อง CLI (cli://) ใช้โควตาแพ็กเกจที่ผู้ใช้ล็อกอินไว้ ไม่ใช่ "ฟรี": จัดอันดับเหมือนราคาไม่ทราบ (ไม่ให้ชนะช่อง API ทุกครั้ง) ส่วนการนับงบยังนับเป็น $0
    if str(r[2]).startswith("cli://"):
        return None
    return None if r[0] is None or r[1] is None else r[0] + r[1]


def rank(c, cands, tier, score_fn, ms_fn, busy_fn=None, gw_fn=None, avoid_gw=None):
    """เรียงช่องสำหรับระดับงาน: light/standard = ถูกที่สุดในกลุ่มที่ฝีมือถึงเกณฑ์ก่อน, heavy = ฝีมือสูงสุดก่อน แล้วค่อยดูราคา"""
    cands = healthy(c, cands)
    sc = {s: score_fn(s) for s in cands}
    ok = [s for s in cands if sc[s] >= BAR[tier]] or sorted(cands, key=lambda s: -sc[s])[:1]
    pool = ok + [s for s in cands if s not in ok]  # ที่ไม่ถึงเกณฑ์เก็บไว้เป็นตัวสำรองท้ายแถว

    def cost_rank(s):
        u = unit_cost(c, s)
        return 1e-6 if u is None else u  # ไม่ทราบราคา: ถือว่าแพงกว่าฟรีแต่ถูกกว่าตัวที่มีราคา
    def key(s):
        met = 0 if s in ok else 1
        tail = ((gw_fn(s) == avoid_gw) if gw_fn else 0, busy_fn(s) if busy_fn else 0, ms_fn(s))
        if tier == "heavy":
            return (met, -round(sc[s], 1), cost_rank(s)) + tail
        return (met, cost_rank(s), -round(sc[s], 1)) + tail
    return sorted(pool, key=key)


def next_tier(c, tier, cands, used_seat, score_fn, ms_fn):
    """ระดับถัดไปที่จะเปลี่ยนตัวเลือกจริง (ถ้าอันดับ 1 ยังเป็นช่องเดิม การยกระดับไม่มีความหมาย)"""
    if tier not in TIERS:
        return None
    for t in TIERS[TIERS.index(tier) + 1:]:
        o = rank(c, cands, t, score_fn, ms_fn)
        if o and o[0] != used_seat:
            return t
    return None


# ---------- รายงาน / คำสั่ง ----------
def _fmt_cap(v, usd=False):
    return "ไม่จำกัด" if v is None else (f"${v:,.2f}" if usd else f"{v:,.0f}")


def status_lines(c):
    now = time.time()
    out = [f"เปิดใช้ guardrails: {'ใช่' if get(c, 'guard:enabled') == '1' else 'ไม่'} | เตือนที่ {get(c, 'guard:warn_pct')}% | ราคาไม่ทราบ+เพดาน USD: {get(c, 'guard:unpriced')} | "
           f"routing: {'เปิด' if get(c, 'route:enabled') == '1' else 'ปิด'} ยกระดับเมื่อตรวจไม่ผ่าน: {'เปิด' if get(c, 'route:escalate') == '1' else 'ปิด'}"]
    for scope, label in (("day", "วันนี้"), ("month", "เดือนนี้")):
        ct, cu = caps(c, scope)
        tok, usd, unp = used(c, _start(scope, now))
        out.append(f"{label}: โทเคน {tok:,.0f} / {_fmt_cap(ct)} | USD ${usd:.4f} / {_fmt_cap(cu, True)}" + (f" | ไม่มีราคา {unp:,.0f} โทเคน (ไม่รวมใน USD)" if unp else ""))
    cc = num(get(c, "cap:claude_day_tokens"))
    cl = used(c, _start("day", now), None, "claude")[0]
    out.append(f"Claude subagent วันนี้ (บันทึกด้วย claude-log): {cl:,.0f} โทเคน / เพดานเตือน {_fmt_cap(cc)}")
    ct, cu = caps(c, "job", "_")
    out.append(f"เพดานต่องาน (ค่ากลาง): โทเคน {_fmt_cap(ct)} | USD {_fmt_cap(cu, True)}")
    jc = c.execute("select k,v from settings where k like 'jobcap:%' order by k").fetchall()
    for k, v in jc:
        out.append(f"  {k} = {v}")
    stale = c.execute("select count(*) from spend where state in ('reserved','expired') and t<?", (now - STALE_S,)).fetchone()[0]
    if stale:
        out.append(f"มีการจองค้าง {stale} รายการ (โปรเซสเคยตายกลางทาง) นับตามค่าประมาณอยู่ — ล้างด้วย: team.py budget reap")
    return out


def spend_report(c, days=7):
    since = time.time() - days * 86400
    out = [f"การใช้งาน {days} วันล่าสุด (settled; src=est คือไม่มี usage จากผู้ให้บริการ)"]
    rows = c.execute("""select seat, model, count(*), sum(coalesce(in_tok,0)), sum(coalesce(out_tok,0)), sum(cost), sum(cost is null), sum(src='est')
        from spend where state='settled' and family='team' and t>=? group by seat, model order by 4+5 desc""", (since,)).fetchall()
    if not rows:
        out.append("  (ยังไม่มีข้อมูล — ข้อมูลเริ่มเก็บหลังเปิดใช้ guardrails)")
    for seat, model, n, ti, to, cost, unp, est in rows:
        out.append(f"  {seat:<10} {str(model)[:34]:<34} {n:>5} ครั้ง  เข้า {ti:>9,} ออก {to:>9,}  " + (f"${cost:.4f}" if cost is not None else "ไม่มีราคา") + (f"  ({unp} ครั้งไม่มีราคา)" if unp and cost is not None else "") + (f"  est×{est}" if est else ""))
    return out


def budget_cmd(c, args):
    """args: ['show'] | ['set', ชื่อ, ค่า] | ['job', งาน, 'tokens=N', 'usd=X'] | ['reap']"""
    act = args[0] if args else "show"
    if act == "set":
        if len(args) != 3 or args[1] not in KEYS:
            raise ValueError("ใช้: budget set <" + "|".join(KEYS) + "> <ค่า|off>")
        k, v = KEYS[args[1]], args[2].lower()
        if v in ("on", "off") and k in ("guard:enabled", "route:enabled", "route:escalate"):
            v = "1" if v == "on" else "0"
        elif k == "guard:unpriced":
            if v not in ("block", "allow"):
                raise ValueError("unpriced ต้องเป็น block หรือ allow")
        elif v != "off":
            if float(v) < 0:
                raise ValueError("ค่าต้องไม่ติดลบ")
        c.execute("insert or replace into settings values(?,?)", (k, v)); c.commit()
    elif act == "job":
        if len(args) < 3:
            raise ValueError("ใช้: budget job <ชื่องาน> tokens=N usd=X")
        for kv in args[2:]:
            name, _, v = kv.partition("=")
            if name not in ("tokens", "usd") or not v:
                raise ValueError(f"ไม่รู้จัก '{kv}' (ใช้ tokens= หรือ usd=)")
            if v != "off" and float(v) < 0:
                raise ValueError("ค่าต้องไม่ติดลบ")
            c.execute("insert or replace into settings values(?,?)", (f"jobcap:{args[1]}:{name}", v))
        c.commit()
    elif act == "reap":
        n = c.execute("update spend set state='failed' where state in ('reserved','expired') and t<?", (time.time() - STALE_S,)).rowcount
        c.commit(); return [f"ล้างการจองค้าง {n} รายการ"]
    elif act != "show":
        raise ValueError("ใช้: budget [show|set|job|reap]")
    return status_lines(c)


# ---------- decide(): ใครควรทำงานนี้ — Claude (subagent ใน Claude Code) หรือทีม AI ฟรี (ai-team) ----------
# ทีม AI ใน ai-team เป็นแชทล้วน (ไม่มีเครื่องมือ กดหน้าเว็บ/แก้ไฟล์ไม่ได้) งานที่ต้องลงมือจึงเป็นของ Claude subagent เสมอ
# กฎทั้งหมดเป็นฮิวริสติกจากคำในโจทย์ + ผลเช็ก ไม่ใช่ผลวัดฝีมือโมเดล: ผู้สั่งชี้ขาดได้ด้วย --tools / lanes set และดูผลจริงที่ `lanes`
CLAUDE = ("haiku", "sonnet", "opus", "fable")
STAKES = re.compile(r"(?i)(?<!ก)ลบ|(?<!ปุ่ม)(?<!ช่อง)(?<!ฟอร์ม)(?<!หน้า)ส่งอีเมล|(?<!ปุ่ม)(?<!ช่อง)(?<!ฟอร์ม)(?<!หน้า)ส่งข้อความ|จ่ายเงิน|ชำระ|โอนเงิน|คืนเงิน|อนุมัติ|ยกเลิกคำสั่งซื้อ|publish|deploy|production|\bprod\b|delete|drop table|refund|payment|send (an )?e-?mail|force.?push")
JUDG = re.compile(r"(?i)ตัดสินใจ|เลือกแนวทาง|ออกแบบ|วางแผน|trade-?off|สถาปัตยกรรม|architecture|รีวิว|review|ประเมิน|วิเคราะห์|ควรหรือไม่|root cause|strategy|กลยุทธ์|ข้อดีข้อเสีย")
ACT = re.compile(r"(?i)กด(?!ดัน)|คลิก|click|กรอก|fill (in|out)|เปิดหน้า|เข้าเว็บ|browser|เบราว์เซอร์|scrape|ล็อกอิน|login|ทุกใบ|ทุกแถว|ทุกหน้า|หลายหน้า|แก้ไฟล์|รันเทส|run tests")
TOOLS = re.compile(r"(?i)กด(?!ดัน)|คลิก|click|กรอก|fill (in|out)|เปิดหน้า|เข้าเว็บ|browser|เบราว์เซอร์|แก้ไฟล์|รันเทส|รันคำสั่ง|terminal|ล็อกอิน|login")
AUTO_CHK = ("json", "notempty", "contains:", "regex:", "py:", "spec")


def profile(brief, chk=None, skill=None, tools=None):
    """คืน dict: lane (action|judgment|bulk|general), stakes, needs_tools, reason[]"""
    b = brief or ""
    stakes = bool(STAKES.search(b))
    j, a = len(JUDG.findall(b)), len(ACT.findall(b))
    why = []
    if stakes:
        lane = "judgment"; why.append("เข้าข่ายย้อนกลับไม่ได้/กระทบคนนอก")
    elif j > a and j == 1 and a == 0 and len(b) < 2500 and (chk or "").startswith(("json", "notempty", "contains:", "regex:")):
        lane = "bulk"; why.append("สัญญาณตัดสินใจอ่อน (1 คำ) และมีตัวเช็กอัตโนมัติ → ถือเป็นงานร่าง")
    elif j > a:
        lane = "judgment"; why.append(f"ต้องตัดสินใจ/ชั่งเหตุผล ({j} คำชี้)")
    elif a > j:
        lane = "action"; why.append(f"งานลงมือเยอะ ({a} คำชี้)")
    elif a == j and a > 0:
        lane = "general"; why.append("ทั้งลงมือและตัดสินใจพอกัน")
    elif len(b) < 2500 and (skill in ("short_answer", "text", "data_clean") or (chk or "").startswith(("json", "notempty", "contains:", "regex:"))):
        lane = "bulk"; why.append("งานเบา/ตรวจผลอัตโนมัติได้")
    else:
        lane = "general"; why.append("ไม่เข้าลักษณะเด่น")
    nt = {"yes": True, "no": False}.get(tools)
    if nt is None and (chk or "").startswith(("py:", "spec", "json")):
        nt = False  # ผลเป็นข้อความที่ตรวจในกล่องทราย (เช่น โค้ด/สเปก Odoo เป็น JSON) ทีมแชททำได้ ต้องการเครื่องมือจริงให้ระบุ --tools yes
    if nt is None:
        nt = bool(TOOLS.search(b)) or lane == "action"
    return {"lane": lane, "stakes": stakes, "needs_tools": nt, "reason": why}


def _up(c, model):
    """ขั้นถัดไปของโมเดล Claude (haiku→sonnet→opus→top ถ้าตั้งไว้) None = ไม่มีแล้ว"""
    chain = ["haiku", "sonnet", "opus"] + ([get(c, "exec:top")] if get(c, "exec:top") in CLAUDE else [])
    return chain[chain.index(model) + 1] if model in chain[:-1] else None


CODE_EDIT = re.compile(r"(?i)refactor|แก้บั๊ก|แก้\s*bug|fix\s+(?:the\s+)?bug|เพิ่มฟีเจอร์|implement|เปลี่ยนลายเซ็น|ลบฟังก์ชัน|แก้ไขฟังก์ชัน|แก้ฟังก์ชัน|แก้ไฟล์|ปรับโค้ด|เขียนโค้ดเพิ่ม|migrate|เปลี่ยน(?:ชื่อ|พารามิเตอร์)")


def lane_stats(c, lane):
    """ผลจริงของทีมฟรีในเลนนี้จาก decide-result: (จำนวนงานที่รายงานผลแล้ว, อัตราผ่าน) หรือ None ถ้ายังไม่มี"""
    n, p = c.execute("select count(*), coalesce(sum(outcome='pass'),0) from decisions where lane=? and executor='team' and outcome is not null and coalesce(job,'')!='shadow'", (lane,)).fetchone()  # job 'shadow' = โจทย์สังเคราะห์ ไม่นับเป็นผลงานจริงของเลน
    return (n, p / n) if n else None


def decide(c, brief, chk=None, skill=None, tools=None, job=None, failed=0, prev=None, team_seats=None):
    """ตัดสินว่างานนี้ควรให้ใครทำ คืน dict (บันทึกลงตาราง decisions ด้วย)
    executor: 'subagent:<haiku|sonnet|opus|fable>' = Claude Code เรียก Agent(model=…) | 'team' = ส่งเข้า ai-team ด้วย submit | 'ask-user' = ยกระดับสุดทางแล้ว"""
    pr = profile(brief, chk, skill, tools)
    tier, tier_why = classify(brief, chk, skill)
    if pr["stakes"]:
        tier = "heavy"
    why = list(pr["reason"])
    lane = pr["lane"]
    verifiable = bool(chk) and (chk.startswith(AUTO_CHK))
    after = int(get(c, "route:escalate_after") or 2)

    def claude(model):
        return {"executor": "subagent:" + model, "model": model}
    mode = get(c, "route:mode") or "free-first"
    st = lane_stats(c, lane)
    learned_bad = bool(st and st[0] >= int(get(c, "route:learn_min") or 5) and st[1] < float(get(c, "route:free_min_pass") or 0.6))
    free = {"executor": "team", "pool": get(c, "exec:free_pool"), "tier": tier}
    if pr["needs_tools"]:
        d = claude(get(c, "exec:" + lane) or "sonnet")
        why.append("ต้องใช้เครื่องมือ (กด/แก้ไฟล์/รันคำสั่ง) ทีม AI แชทล้วนทำไม่ได้")
    elif lane == "judgment" and (pr["stakes"] or mode != "free-first"):
        d = claude(get(c, "exec:judgment") or "opus")
        why.append("ตัดสินใจ/เสี่ยงสูง ไม่ปล่อยให้โมเดลฟรีเป็นคนชี้ขาด")
        if pr["stakes"]:
            why.append("ให้ Opus ตัดสิน/ตรวจแผนก่อน ผู้ใช้ยืนยัน แล้วค่อยส่งขั้นลงมือจริงให้ Sonnet (ถามซ้ำด้วย --tools yes)")
            if mode == "free-first" and not (team_seats is not None and not team_seats):
                d["pre_draft"] = True; why.append("ให้ทีมฟรีวิเคราะห์ทางเลือก/ความเสี่ยงเป็นสรุปสั้นก่อน (pre_draft) Opus จะอ่านสรุปแทนที่จะสำรวจเอง")
    elif team_seats is not None and not team_seats:
        d = claude("haiku"); why.append("ไม่มีช่องทีม AI ที่พร้อมใช้ → ถอยมาใช้ Claude รุ่นเบา")
    elif learned_bad:
        d = claude(get(c, "exec:" + lane) or "sonnet")
        why.append(f"ผลจริงของทีมฟรีในเลน {lane}: ผ่าน {st[1]:.0%} จาก {st[0]} งาน (< {float(get(c, 'route:free_min_pass')):.0%}) → ข้ามไป Claude ตรง ไม่เสียรอบฟรีที่มักล้ม")
    elif lane == "judgment":
        d = dict(free, tier="heavy", skill=skill or "decision_calc"); tier = "heavy"; d["head_review"] = True
        why.append("free-first: ตัดสินใจแต่ไม่เสี่ยงสูง → ทีมฟรีที่ฝีมือสูงสุดทำเต็มกำลังก่อน (ไม่ลดการคิด) Head ตรวจผลก่อนใช้ ล้มซ้ำค่อยยกเป็น Sonnet→Opus")
    elif lane == "bulk" or (tier in ("light", "standard") and verifiable):
        d = free
        why.append(f"ตรวจผลอัตโนมัติได้/งานเบา → ทีม AI ฟรีทำก่อน ไม่ผ่านค่อยยกระดับ (ระดับ {tier}: {tier_why})")
    else:
        d = free
        why.append(f"(ระดับ {tier}: {tier_why}) ไม่มีตัวเช็กอัตโนมัติ → ทีมร่าง แล้ว Head ตรวจเองก่อนใช้ (ผู้ตรวจ AI ไม่ใช่ด่านสุดท้าย)")

    # ยกระดับเมื่อล้มซ้ำ: team → sonnet → opus → top → ask-user
    if failed >= after:
        cur = prev or (d.get("model") if d["executor"].startswith("subagent") else "team")
        nxt = "sonnet" if cur in ("team", None, "") else _up(c, cur)
        if nxt:
            d = claude(nxt); why.append(f"ล้ม {failed} ครั้งที่ {cur} → ยกเป็น {nxt}")
        else:
            d = {"executor": "ask-user"}; why.append(f"ล้ม {failed} ครั้งที่ {cur} และไม่มีขั้นสูงกว่า → ให้ผู้ใช้ตัดสิน")

    # เพดานสัดส่วน Opus ต่องาน (เว้นแต่งานเสี่ยงสูง): กัน Opus กินงบเพราะตีเลนเป็น judgment ง่ายเกินไป
    if d.get("model") == "opus" and job and not pr["stakes"] and failed < after:
        row = c.execute("select count(*), coalesce(sum(executor='subagent:opus'),0) from decisions where job=?", (job,)).fetchone()
        if row[0] >= 4 and (row[1] + 1) / (row[0] + 1.0) > float(get(c, "route:opus_share") or 0.3):
            d = claude("sonnet"); why.append(f"สัดส่วน Opus ของงาน '{job}' เกิน {float(get(c, 'route:opus_share')):.0%} → ใช้ Sonnet (Opus เก็บไว้ที่จุดตัดสินใจจริง)")

    if CODE_EDIT.search(brief or "") and d["executor"] != "ask-user":
        d["use_skill"] = "reasoning-structure"
        why.append("เป็นงานแก้โค้ด → ทำ Reasoning Brief ก่อนลงมือ (skill reasoning-structure: blast.py + pre-mortem + convention + atomic plan แล้วรัน brief_check.py ให้ผ่าน)")
    did = c.execute("insert into decisions(t,job,lane,tier,executor,stakes,needs_tools,reason) values(?,?,?,?,?,?,?,?)",
                    (time.time(), job, lane, tier, d["executor"], int(pr["stakes"]), int(pr["needs_tools"]), "; ".join(why))).lastrowid
    c.commit()
    d.update({"id": did, "lane": lane, "tier": tier, "stakes": pr["stakes"], "needs_tools": pr["needs_tools"], "reason": why})
    if d["executor"] == "team":
        try:  # จัดทีมให้เหมาะกับงาน (ผู้ทำ+ผู้ตรวจ ถ้างานต้องการ) — ล้มเหลวต้องไม่ทำให้ decide พัง
            import crew
            cr = crew.assemble(c, brief, chk, d.get("skill") or skill, d["tier"], False)
            d["crew"] = cr
        except Exception:
            cr = None
        if skill and not d.get("skill"):
            d["skill"] = skill
        d["how"] = f'team.py submit "<brief>" --pool {d["pool"]} --tier {tier}' + (f' --needs {d["skill"]}' if d.get("skill") else "") + (f' --check {chk}' if chk else "") + (f' --job {job}' if job else "")
        if d.get("crew") and d["crew"]["mode"] == "maker+reviewer":
            d["how"] = d["crew"]["how"] + (f" --job {job}" if job else "")  # ทีมผู้ทำ+ผู้ตรวจที่จัดให้งานนี้
    elif d.get("model"):
        d["how"] = f'Agent(subagent_type="general-purpose", model="{d["model"]}")'
        if d.get("pre_draft"):
            d["pre_draft_how"] = 'team.py submit "วิเคราะห์ทางเลือกและความเสี่ยงของงานต่อไปนี้ สรุปไม่เกิน 15 บรรทัด: <brief>" --pool ' + (get(c, "exec:free_pool") or "@auto") + " --tier heavy" + (f" --job {job}" if job else "")
    return d


def set_result(c, did, outcome):
    if outcome not in ("pass", "fail"):
        raise ValueError("outcome ต้องเป็น pass หรือ fail")
    if not c.execute("select 1 from decisions where id=?", (did,)).fetchone():
        raise ValueError(f"ไม่พบ decision #{did}")
    c.execute("update decisions set outcome=? where id=?", (outcome, did)); c.commit()


def lanes_cmd(c, args):
    """lanes | lanes set <action|judgment|general|bulk|top|free_pool> <ค่า>"""
    if args and args[0] == "set":
        if len(args) == 3 and args[1] == "mode":
            if args[2] not in ("free-first", "balanced"):
                raise ValueError("mode ต้องเป็น free-first หรือ balanced")
            c.execute("insert or replace into settings values('route:mode',?)", (args[2],)); c.commit()
            return lanes_cmd(c, [])
        if len(args) != 3 or args[1] not in ("action", "judgment", "general", "bulk", "top", "free_pool"):
            raise ValueError("ใช้: lanes set <action|judgment|general|bulk|top|free_pool> <haiku|sonnet|opus|fable|ชื่อกลุ่มช่อง> หรือ lanes set mode <free-first|balanced>")
        if args[1] != "free_pool" and args[2] not in CLAUDE + ("",) and not (args[1] == "top" and args[2] == "off"):
            raise ValueError("โมเดลต้องเป็น " + "|".join(CLAUDE))
        c.execute("insert or replace into settings values(?,?)", (f"exec:{args[1]}", "" if args[2] == "off" else args[2])); c.commit()
    out = [f"โหมด {get(c, 'route:mode')} · เลน → ผู้ทำ: " + " · ".join(f"{k}={get(c, 'exec:' + k) or '(ถามผู้ใช้)'}" for k in ("action", "general", "judgment", "bulk", "top")) +
           f" · งานแชทล้วนที่ตรวจได้ → ทีมฟรี {get(c, 'exec:free_pool')} · เพดานสัดส่วน Opus {float(get(c, 'route:opus_share')):.0%}"]
    tot, nt, nc = c.execute("select count(*), coalesce(sum(executor='team'),0), coalesce(sum(executor like 'subagent:%'),0) from decisions where coalesce(job,'')!='shadow'").fetchone()
    if tot:
        tt, ct = used(c, time.time() - 7 * 86400, None, "team")[0], used(c, time.time() - 7 * 86400, None, "claude")[0]
        out.append(f"สัดส่วนงานที่ตัดสินไว้: ทีมฟรี {nt}/{tot} ({nt / tot:.0%}) · Claude {nc}/{tot} ({nc / tot:.0%}) · โทเคน 7 วัน: ทีมฟรี {tt:,.0f} · Claude {ct:,.0f}")
    for lane_ in ("action", "judgment", "general", "bulk"):
        st_ = lane_stats(c, lane_)
        if st_:
            out.append(f"  เรียนรู้: เลน {lane_} ทีมฟรีผ่าน {st_[1]:.0%} จาก {st_[0]} งานที่รายงานผล" + (" → จะข้ามไป Claude ตรง" if st_[0] >= int(get(c, "route:learn_min") or 5) and st_[1] < float(get(c, "route:free_min_pass") or 0.6) else ""))
    rows = c.execute("select lane, executor, count(*), sum(outcome='pass'), sum(outcome='fail'), sum(outcome is null) from decisions where coalesce(job,'')!='shadow' group by lane, executor order by lane, executor").fetchall()
    if rows:
        out.append("ผลจริงที่บันทึกไว้ (decide-result):")
        for lane, ex, n, p, f, u in rows:
            out.append(f"  {lane:<9} {ex:<18} {n:>3} งาน  ผ่าน {p or 0}  ไม่ผ่าน {f or 0}  ยังไม่รายงานผล {u or 0}")
    return out


# ---------- นับโทเคนของ Claude subagent + รายงานต้นทุนยกระดับ ----------
def claude_log(c, model, tokens, job=None, decision_id=None):
    """บันทึกโทเคนรวมของ Claude subagent (ค่า subagent_tokens ที่ Agent tool รายงาน — มีแต่ยอดรวม ไม่แยกเข้า/ออก/คิด)
    นับแยกจากทีม ai-team (family='claude') ไม่ปนเพดานของทีมฟรี"""
    if model not in CLAUDE:
        raise ValueError("โมเดลต้องเป็น " + "|".join(CLAUDE))
    tokens = int(tokens)
    if not 0 < tokens <= 20000000:
        raise ValueError("จำนวนโทเคนต้องอยู่ระหว่าง 1 ถึง 20,000,000")
    lane = None
    if decision_id is not None:
        r = c.execute("select lane, job from decisions where id=?", (decision_id,)).fetchone()
        if not r:
            raise ValueError(f"ไม่พบ decision #{decision_id}")
        lane, job = r[0], job or r[1]
        if c.execute("select 1 from spend where family='claude' and lane is not null and job is ? and model=? and in_tok=? and usage_raw=?", (job, model, tokens, f"decision:{decision_id}")).fetchone():
            raise ValueError(f"บันทึก decision #{decision_id} ({model}, {tokens:,} โทเคน) ไปแล้ว — กันบันทึกซ้ำ")
    c.execute("insert into spend(t,seat,model,job,state,in_tok,out_tok,src,family,lane,usage_raw) values(?,?,?,?,'settled',?,0,'claude-total','claude',?,?)",
              (time.time(), "claude:" + model, model, job, tokens, lane, f"decision:{decision_id}" if decision_id is not None else None))
    c.commit()
    ct = num(get(c, "cap:claude_day_tokens"))
    day = used(c, _start("day", time.time()), None, "claude")[0]
    msg = f"บันทึก Claude {model} {tokens:,} โทเคน (รวมวันนี้ {day:,.0f}" + (f" / เพดาน {ct:,.0f}" if ct else "") + ")"
    if ct and day >= ct:
        msg += " — เกินเพดาน Claude รายวันที่ตั้งไว้ (เป็นการเตือน: Claude subagent ไม่ได้ถูกจองงบล่วงหน้า)"
    return msg


def token_report(c, days=7):
    """โทเคนทั้งหมด: ทีมฟรี (แยกโทเคนคิด) + Claude + ต้นทุนการยกระดับต่อระดับงาน"""
    since = time.time() - days * 86400
    out = [f"โทเคน {days} วันล่าสุด"]
    r = c.execute("""select count(*), coalesce(sum(in_tok),0), coalesce(sum(out_tok),0), coalesce(sum(reason_tok),0),
        sum(reason_tok is not null), sum(src='est'), coalesce(sum(case when reason_tok is not null then out_tok end),0)
        from spend where family='team' and state='settled' and t>=?""", (since,)).fetchone()
    n, ti, to, rt, nr, ne, to_r = r
    out.append(f"  ทีมฟรี  {n or 0} ครั้ง  เข้า {ti:,}  ออก {to:,}" + (f"  (โทเคนคิด {rt:,} = {100.0 * rt / to_r:.0f}% ของโทเคนออกใน {nr} ครั้งที่ผู้ให้บริการแยกให้ อีก {n - nr} ครั้งไม่แยก)" if nr and to_r else "  (ผู้ให้บริการยังไม่แยกโทเคนคิดให้ในช่วงนี้)") + (f"  ประมาณเอง {ne} ครั้ง" if ne else ""))
    for fam_model, cnt, tok in c.execute("select model, count(*), sum(in_tok) from spend where family='claude' and state='settled' and t>=? group by model order by 3 desc", (since,)):
        out.append(f"  Claude {fam_model:<7} {cnt} ครั้ง  รวม {tok:,} (ยอดรวมจาก subagent_tokens ไม่แยกเข้า/ออก)")
    rows = c.execute("""select seat, count(*), sum(in_tok), sum(out_tok), sum(reason_tok), sum(reason_tok is not null) from spend
        where family='team' and state='settled' and t>=? group by seat order by 4 desc limit 8""", (since,)).fetchall()
    if rows:
        out.append("  ต่อช่อง (ทีมฟรี): " + " | ".join(f"{s} {n}ครั้ง ออก {o:,}" + (f" คิด {k:,}" if nr_ else "") for s, n, i, o, k, nr_ in rows))
    # ต้นทุนยกระดับ: งานที่ถูกยกระดับ แยกโทเคนตามระดับที่ใช้ (ระดับต่ำ = ต้นทุนที่เสียไปก่อนล้ม)
    esc = c.execute("select id from tasks where coalesce(escalated,0)>0").fetchall()
    if esc:
        ids = ",".join(str(i[0]) for i in esc)
        rows = c.execute(f"select coalesce(tier,'?'), count(distinct task_id), sum(coalesce(in_tok,0)+coalesce(out_tok,0)) from spend where task_id in ({ids}) and state='settled' group by tier").fetchall()
        out.append("ต้นทุนงานที่ถูกยกระดับ (ต่อระดับ): " + " | ".join(f"{t}: {n} งาน {tok:,} โทเคน" for t, n, tok in rows) + "  → ระดับต่ำคือโทเคนที่เสียไปก่อนล้ม")
    else:
        out.append("ต้นทุนการยกระดับ: ยังไม่มีงานที่ถูกยกระดับในข้อมูล")
    tr = c.execute("select coalesce(tier,'(ไม่ระบุ)'), count(*), sum(coalesce(in_tok,0)+coalesce(out_tok,0)) from spend where family='team' and state='settled' and t>=? group by tier", (since,)).fetchall()
    out.append("ทีมฟรีแยกตามระดับงาน: " + (" | ".join(f"{t} {n}ครั้ง {tok:,}" for t, n, tok in tr) if tr else "-"))
    return out
