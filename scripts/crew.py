# -*- coding: utf-8 -*-
"""จัดทีมให้เหมาะกับงาน: จากโจทย์ (ความยาก, ตัวเช็ก, ความเสี่ยง, ทักษะ) เลือกว่าจะใช้ "กี่คน ใครทำหน้าที่อะไร"

กติกา (ทุกข้อมีเหตุผลแนบในผลลัพธ์ ไม่ใช่กล่องดำ):
  ทำคนเดียว (solo)  — งานเบา หรืองานที่มีตัวเช็กอัตโนมัติและไม่ยาก: ช่องเดียวที่ "ถูกที่สุดที่ฝีมือถึงเกณฑ์" ถ้าล้มค่อยยกระดับ
  ผู้ทำ + ผู้ตรวจ   — งานมาตรฐานที่ไม่มีตัวเช็ก หรืองานหนัก: ผู้ตรวจต้องเป็นคนละช่อง (ถ้าเป็นไปได้คนละเกตเวย์) ฝีมือตรวจถึงเกณฑ์ 0.6
                      แก้ได้ 1 รอบหลังถูกตีกลับ (ผู้ตรวจ AI ตีตกผิดได้: เจอจริงในการทดลอง)
  งานเสี่ยงสูง      — ทีมฟรีร่างเป็น ผู้ทำ+ผู้ตรวจ แต่ "ผู้ชี้ขาด" คือ Claude Opus เสมอ (ผู้ตรวจ AI ไม่ใช่ด่านสุดท้าย)
  ไม่มีตัวเช็กอัตโนมัติ → head_review: Head ต้องอ่านผลก่อนใช้
ความแรงของแต่ละช่อง = คะแนนทักษะที่วัดไว้ (ถ้ามี) ไม่เช่นนั้นคะแนน Ranking (คุณภาพ × ความน่าเชื่อถือ) ไม่เช่นนั้น 0.5 "ยังไม่วัด" — คูณความน่าเชื่อถือเสมอ"""
import re
import routing

REVIEW_BAR = 0.6
ROLE_HINTS = [  # (regex, role) — ตำแหน่งผู้ทำที่เหมาะกับลักษณะงาน (มีในตาราง roles อยู่แล้ว)
    (re.compile(r"(?i)pre-?mortem|จำลองความพัง|จะพังเพราะ"), "premortem"),
    (re.compile(r"(?i)เคสทดสอบ|ทดสอบ(ซอฟต์แวร์|ระบบ)|test case|unit test"), "tester"),
    (re.compile(r"(?i)แปล|translate|สรุป(บทความ|ข้อความ|เนื้อหา)"), "translator"),
    (re.compile(r"(?i)ล้างข้อมูล|ทำความสะอาดข้อมูล|จัดรูปแบบ(ชื่อ|เบอร์|ที่อยู่)|data clean"), "cleaner"),
    (re.compile(r"(?i)วิเคราะห์|เปรียบเทียบ|ประเมิน|ชั่งข้อดีข้อเสีย|analy[sz]e"), "analyst"),
    (re.compile(r"(?i)รวมงาน|รวมเอกสาร|ประกอบเป็นฉบับเดียว"), "editor"),
]


def _strength_fn(c, skill):
    import team as T, rank
    stats = {x["seat"]: x for x in rank.seat_stats(c, T)}

    def strength(seat):
        st = stats.get(seat) or {}
        n_calls = st.get("calls_ok", 0) + st.get("errors", 0)
        rel = st.get("reliability", 1.0) if n_calls >= 5 else 1.0  # ยังเรียกน้อยกว่า 5 ครั้ง: ไม่มีหลักฐานว่าไม่น่าเชื่อถือ จึงไม่ลดคะแนน
        r = c.execute("select score from seat_skills where seat=? and skill=?", (seat, skill)).fetchone() if skill else None
        if r:
            return r[0] * rel, f"ทักษะ {skill} {r[0]:.2f}"
        if st.get("score") is not None:
            return st["score"], f"Ranking {st['score']:.0%}"
        return 0.5 * rel, "ยังไม่วัด"
    return strength, stats


def pick_role(brief, hint=None):
    if hint:
        return hint
    for rx, role in ROLE_HINTS:
        if rx.search(brief or ""):
            return role
    return "maker"


def assemble(c, brief, chk=None, skill=None, tier=None, stakes=None, role=None, max_members=2):
    """คืน dict: members[{role,seat,why,strength}], mode, rounds, flow, how, head_review, final, notes"""
    import team as T
    tier_c, tier_why = routing.classify(brief, chk, skill, hint=tier)
    pr = routing.profile(brief, chk, skill)
    stakes = pr["stakes"] if stakes is None else stakes
    if stakes:
        tier_c = "heavy"
    elif pr["lane"] == "judgment" and tier_c == "light":
        tier_c = "standard"; tier_why += " · งานตัดสินใจ ยกเป็นมาตรฐาน"
    verifiable = bool(chk) and chk.startswith(routing.AUTO_CHK)
    seats = [r[0] for r in c.execute("select s.name from seats s join catalog k on k.name=s.catalog order by s.rowid")]
    gw = lambda s: (c.execute("select k.gateway from seats s join catalog k on k.name=s.catalog where s.name=?", (s,)).fetchone() or ["?"])[0]
    strength, stats = _strength_fn(c, skill)
    strength_rv, _ = _strength_fn(c, "review")  # ผู้ตรวจวัดด้วยทักษะ "review" ไม่ใช่ทักษะของงาน
    notes = []
    if not seats:
        return dict(members=[], mode="none", rounds=0, flow=None, how=None, head_review=True, final="ask-user", tier=tier_c, notes=["ไม่มีช่อง AI ที่พร้อมใช้ในทีม"])

    maker_order = routing.rank(c, seats, tier_c, lambda s: strength(s)[0], lambda s: T.avg_ms(c, s), lambda s: 0, gw, None)
    maker = maker_order[0]
    lead_pick = None
    sq = next((q for q, t in routing.SQUADS.items() if t == tier_c), None)
    lead = routing.leads_get(c).get(sq)
    if lead and lead in seats and not routing.cooling(c, lead):
        lead_pick = lead
        maker = lead
        maker_order = [lead] + [x for x in maker_order if x != lead]
        if strength(lead)[0] < routing.BAR[tier_c]:
            notes.append(f"หัวทีม {lead} ฝีมือ {strength(lead)[0]:.2f} ต่ำกว่าเกณฑ์งานระดับ {tier_c} ({routing.BAR[tier_c]}) แต่คุณเลือกไว้ จึงใช้ตามนั้น")
    elif lead:
        notes.append(f"หัวทีมที่เลือก ({lead}) กำลังพักจาก error ใช้ผู้ทำอันดับถัดไปแทนชั่วคราว")
    mrole = pick_role(brief, role)
    members = [dict(role=mrole, seat=maker, strength=round(strength(maker)[0], 2), why=strength(maker)[1] + (f" · ★ หัวทีม{ {'senior': 'อาวุโส', 'ops': 'ปฏิบัติการ', 'junior': 'ฝึกงาน'}[sq] } ที่คุณเลือก" if lead_pick else f" · {'ถูกที่สุดที่ถึงเกณฑ์' if tier_c != 'heavy' else 'ฝีมือสูงสุดสำหรับงานหนัก'} (เกณฑ์ {routing.BAR[tier_c]})"))]

    solo = (verifiable and tier_c != "heavy") or tier_c == "light"
    if stakes or (pr["lane"] == "judgment" and not verifiable):  # ตัดสินใจโดยไม่มีตัวเช็ก: ต้องมีผู้ตรวจอีกคน
        solo = False
    mode = "solo"
    rounds = 0
    if not solo and max_members >= 2:
        cands = [s for s in seats if s != maker and not routing.cooling(c, s)]
        scored = sorted(((strength_rv(s)[0], s) for s in cands), reverse=True)
        diff = [x for x in scored if gw(x[1]) != gw(maker) and x[0] >= REVIEW_BAR]
        same = [x for x in scored if x[0] >= REVIEW_BAR]
        pick = (diff or same or [None])[0]
        if pick:
            rv = pick[1]
            members.append(dict(role="reviewer", seat=rv, strength=round(pick[0], 2),
                                why=strength_rv(rv)[1] + (" · คนละเกตเวย์กับผู้ทำ (ไม่ล้มพร้อมกัน/ไม่ลำเอียงแบบเดียวกัน)" if gw(rv) != gw(maker) else " · เกตเวย์เดียวกับผู้ทำ (ไม่มีทางเลือกอื่น)")))
            mode = "maker+reviewer"
            rounds = 1  # ผู้ตรวจ AI อาจตีตกผิด: ให้แก้ได้ 1 รอบก่อนปฏิเสธงาน
        else:
            notes.append(f"ไม่มีช่องอื่นที่ฝีมือตรวจถึง {REVIEW_BAR} → ไม่ตั้งผู้ตรวจ (ใช้ตัวเช็กอัตโนมัติ/Head ตรวจแทน)")
    elif solo:
        notes.append("ทำคนเดียว: " + ("งานเบา" if tier_c == "light" else "มีตัวเช็กอัตโนมัติและไม่ใช่งานหนัก"))
    used_ = {m["seat"] for m in members}
    fallback = [s for s in maker_order[1:] if s not in used_][:2]
    head_review = not verifiable
    final = "opus" if stakes else ("ตัวเช็กอัตโนมัติ" if verifiable else "Head")
    if stakes:
        notes.append("เสี่ยงสูง: ทีมฟรีร่าง/ตรวจ แต่ผู้ชี้ขาดคือ Claude Opus และต้องให้ผู้ใช้ยืนยันก่อนลงมือ")
    if fallback:
        notes.append("ตัวสำรองผู้ทำถ้าล้ม: " + ", ".join(fallback) + " (แล้วยกระดับ Claude ตามกติกา decide)")
    flow = None
    how = None
    if mode == "maker+reviewer":
        flow = f"{members[0]['role']}@{members[0]['seat']}>reviewer@{members[1]['seat']}"
        how = f'team.py submit "<brief>" --flow "{flow}" --rounds {rounds} --tier {tier_c}' + (f" --needs {skill}" if skill else "") + (f" --check {chk}" if chk else "")
    else:
        how = f'team.py submit "<brief>" --pool {",".join([maker] + fallback)} --tier {tier_c}' + (f" --needs {skill}" if skill else "") + (f" --check {chk}" if chk else "") + (f" --as {mrole}" if mrole != "maker" else "")
    est = [stats.get(m["seat"], {}).get("avg_out_tok") for m in members]
    return dict(members=members, mode=mode, rounds=rounds, flow=flow, how=how, tier=tier_c, tier_why=tier_why, stakes=bool(stakes), verifiable=verifiable,
                head_review=head_review, final=final, fallback=fallback, notes=notes,
                est_out_tokens=int(sum(e or 0 for e in est)) if any(est) else None)
