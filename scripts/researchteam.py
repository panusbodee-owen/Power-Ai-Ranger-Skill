# -*- coding: utf-8 -*-
"""ทีม Research: ค้นเว็บจริง (TinySearch) แล้วให้ทีม AI ประเมินธุรกิจ/โอกาสหลายทาง เทียบกัน และจัดอันดับ
ขั้น 1 ผู้วางแผนเสนอตัวเลือก+คำค้น  ขั้น 2 โค้ดค้นเว็บเก็บหลักฐาน [W#]  ขั้น 3 ผู้วิเคราะห์ประเมินทีละตัวเลือกขนาน
ขั้น 4 บรรณาธิการเทียบ/จัดอันดับ + ผู้ตรวจ  ขั้น 5 โค้ดประกอบรายงาน (ผลประเมินดิบแนบท้าย พร้อมแหล่งอ้างอิง)
ทีมไม่มีตัวเลขตลาดของตัวเอง: ตัวเลขทุกตัวต้องมาจากหลักฐานที่ค้นมา ไม่งั้นต้องติดป้าย [สมมติ]"""
import io, json, os, time
import team, webtools

CONTEXT = ("บริบทผู้ถาม: ผู้ก่อตั้งคนเดียวในประเทศไทย ทำงานกับทีม AI ทุนน้อย ไม่ใช้งบโฆษณา มีพื้นฐานระบบ ERP/Odoo และเว็บแอป Supabase+Netlify "
           "ต้องการธุรกิจ/ผลิตภัณฑ์ที่ลูกค้าจ่ายจริง เริ่มได้เล็กๆ และไม่ชนกฎหมายหรือข้อตกลงที่มีอยู่")
CRITERIA = "ความเจ็บปวดเรื่องเงินของลูกค้า, ความสามารถจ่าย, เข้าถึงลูกค้าโดยไม่ใช้งบโฆษณา, ระดับการแข่งขัน/คู่แข่งฟรี, ความเสี่ยงกฎหมาย/กฎระเบียบ, ภาระ support ของคนเดียว, ความเหมาะกับทีม AI"


def _result(c, tid):
    return c.execute("select status,result,used_seat,error from tasks where id=?", (tid,)).fetchone()


def plan(c, topic, n, name):
    brief = (f"{CONTEXT}\n\nงาน (ผู้วิเคราะห์): หัวข้อวิจัย: {topic}\nเสนอ {n} ตัวเลือกธุรกิจ/ผลิตภัณฑ์ที่แตกต่างกันจริง (คนละกลุ่มลูกค้าหรือคนละปัญหา) ที่ควรตรวจสอบ "
             "ให้แต่ละตัวมีคำค้นภาษาไทยสั้นๆ 1 คำค้น ที่จะใช้ค้นหาข้อมูลตลาด/คู่แข่ง/ราคา/กฎระเบียบของตัวเลือกนั้นในไทย "
             'ตอบเป็น JSON เท่านั้น: {"candidates":[{"name":"ชื่อตัวเลือก","query":"คำค้น"}]}')
    tid = team.submit(c, brief, "@auto", f"{name}-plan", "json", None, 0, "analyst")
    team.worker(8, 3, drain=True)
    st, res, _, err = _result(c, tid)
    data = team.first_json(res or "") if res else None
    items = (data or {}).get("candidates") if isinstance(data, dict) else None
    items = [x for x in (items or []) if isinstance(x, dict) and x.get("name") and x.get("query")][:n]
    if len(items) < 3:
        raise ValueError(f"ผู้วางแผนเสนอตัวเลือกไม่พอ ({st}) — รันใหม่ได้")
    return items


def run(topic, out, n=6, name=None):
    c = team.db()
    name = name or "research-" + time.strftime("%m%d-%H%M")
    items = plan(c, topic, n, name)
    ev, subs = [], []
    for it in items:                       # ขั้น 2: ค้นเว็บ (โค้ด ไม่ใช้โมเดล) เว้นช่วงกันชนเพดานความเร็วฟรี
        try:
            pack = webtools.fact_pack(it["query"], 5, 1)
        except Exception as e:
            pack = f"(ค้นเว็บไม่สำเร็จ: {e})"
        ev.append(pack[:9000]); time.sleep(13)
    tids = []
    for it, pack in zip(items, ev):        # ขั้น 3: ประเมินทีละตัวเลือกขนาน
        brief = (f"{pack}\n\n=====\n{CONTEXT}\n\nงาน (ผู้วิเคราะห์): ประเมินตัวเลือก \"{it['name']}\" จากหลักฐานข้างบนเท่านั้น ไม่เกิน 220 คำ ภาษาไทย "
                 f"ให้คะแนน 1-5 ต่อเกณฑ์ ({CRITERIA}) แต่ละเกณฑ์ใส่เหตุผลสั้นๆ และรหัสหลักฐาน [W#] ถ้าไม่มีหลักฐานให้เขียน [สมมติ] และคะแนนห้ามเกิน 3 "
                 "ห้ามสร้างตัวเลขตลาด/ราคา/เปอร์เซ็นต์ที่ไม่อยู่ในหลักฐาน แหล่งโซเชียลน่าเชื่อถือต่ำกว่าเว็บราชการ/สื่อ ปิดท้ายด้วยบรรทัด 'คะแนนรวม: X/35' และ 'สิ่งที่ต้องไปตรวจจริง: ...'")
        tids.append(team.submit(c, brief, "@auto", f"{name}-cand", None, None, 0, "analyst"))
    team.worker(8, 3, drain=True)
    for it, tid in zip(items, tids):
        st, res, seat, err = _result(c, tid)
        subs.append((it, st, (res or "").strip(), seat, err))
    ok = [(it, r) for it, st, r, _, _ in subs if st == "done" and r]
    if len(ok) < 3:
        raise ValueError("ประเมินสำเร็จน้อยกว่า 3 ตัวเลือก — รันใหม่ได้")
    body = "\n\n".join(f"### {it['name']}\n{r}" for it, r in ok)
    brief = (f"ผลประเมินของแต่ละตัวเลือก (ข้อมูลอ้างอิง):\n\n{body}\n\n=====\n{CONTEXT}\n\n"
             "งาน (บรรณาธิการ): จากผลประเมินข้างบนเท่านั้น เขียนภาษาไทยไม่เกิน 500 คำ: (1) ตารางจัดอันดับตัวเลือกตามคะแนนรวม พร้อมจุดเด่น/จุดอ่อนที่สุดของแต่ละตัว "
             "(2) ตัวเลือกที่ควรตรวจสอบก่อน 2 อันดับแรก พร้อมเหตุผลและการทดลอง 2 สัปดาห์ที่ไม่ใช้เงินโฆษณา (3) จุดที่หลักฐานอ่อนหรือขัดกัน ห้ามเพิ่มตัวเลขหรือข้อเท็จจริงใหม่ "
             "เกณฑ์ตรวจ: ไม่มีตัวเลขนอกผลประเมิน, อันดับสอดคล้องกับคะแนนรวมที่ให้ไว้, ระบุว่าอะไรยังเป็นสมมติ")
    tid = team.submit(c, brief, None, f"{name}-editor", None, team.parse_flow("editor@@auto>reviewer@seat5"), 1)
    team.worker(8, 3, drain=True)
    st, res, seat, err = _result(c, tid)
    L = [f"# ทีม Research: {topic}", "", f"> สร้างโดยทีม AI + ค้นเว็บจริง (TinySearch) · บรรณาธิการ ช่อง {seat} · สถานะตรวจ: {st}",
         "> ตัวเลขทุกตัวต้องมาจากหลักฐาน [W#] ในผลประเมินท้ายเอกสาร ที่ไม่มีหลักฐานติดป้าย [สมมติ] — ยังไม่ใช่ข้อสรุปให้ลงทุน ต้องตรวจจริงตามหัวข้อ 'สิ่งที่ต้องไปตรวจจริง'", ""]
    L += ["## สรุปและจัดอันดับ", "", (res or f"(บรรณาธิการไม่สำเร็จ: {st} {err})").strip(), "", "---", "", "## ผลประเมินรายตัวเลือก", ""]
    for it, st2, r, seat2, err2 in subs:
        L += [f"### {it['name']}  \n*คำค้น: {it['query']} · ผู้ประเมิน: {seat2 or '-'} · {st2}*", "", r or f"(ไม่สำเร็จ: {err2})", ""]
    L += ["---", "", "## หลักฐานที่ค้นมา (แหล่งอ้างอิง)", ""]
    for it, pack in zip(items, ev):
        L += [f"### {it['name']}", "```", pack[:5000], "```", ""]
    io.open(out, "w", encoding="utf-8").write("\n".join(L))
    return st, name, out
