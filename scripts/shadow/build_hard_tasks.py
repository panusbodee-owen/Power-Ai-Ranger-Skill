# -*- coding: utf-8 -*-
"""สร้าง hard_tasks.json: โจทย์ตัดสินใจที่ต้องคำนวณ/ไล่เงื่อนไขหลายขั้น ตัวเลือกลวงเป็นข้อผิดที่คนมักทำ (ลืมคิดลดค่าเงิน, ละเลยข้อจำกัด ฯลฯ)
เฉลยคำนวณด้วยโปรแกรมทุกข้อ ไม่ใช่ความเห็นของผู้เขียน"""
import itertools, json, math, os, random
R = random.Random(2026)
L = "ABC"; tasks = []
fmt = lambda x: f"{x:,.0f}"


def add(tid, sit, opts, correct, note):
    """opts = ข้อความตัวเลือก, correct = index ที่ถูก (สลับตำแหน่งด้วย seed)"""
    order = list(range(len(opts))); R.shuffle(order)
    body = "\n".join(f"{L[i]}) {opts[o]}" for i, o in enumerate(order))
    tasks.append({"id": tid, "brief": f"สถานการณ์: {sit}\nตัวเลือก:\n{body}\nให้เลือก 1 ตัวเลือก ตอบเป็น JSON บรรทัดเดียวเท่านั้น: "
                  '{"choice":"A|B|C","reason":"เหตุผลไม่เกิน 3 ประโยค แสดงการคำนวณสำคัญ"}', "answer": L[order.index(correct)], "groups": [], "note": note})


# 1) ต้นทุนรวมปัจจุบัน (คิดลดเงิน): อันดับตามยอดรวมไม่คิดลด ต้องต่างจากอันดับตามมูลค่าปัจจุบัน (ล่อให้ลืมคิดลด)
n = 0
while n < 3:
    r, yrs = R.choice([0.08, 0.10, 0.12]), R.choice([5, 6, 8])
    af = (1 - (1 + r) ** -yrs) / r
    plans = [(R.randrange(200, 900, 50) * 1000, R.randrange(40, 160, 5) * 1000) for _ in range(3)]
    pv = [u + a * af for u, a in plans]; raw = [u + a * yrs for u, a in plans]
    srt = sorted(pv)
    if pv.index(min(pv)) == raw.index(min(raw)) or srt[1] / srt[0] < 1.04:
        continue
    n += 1
    add(f"H{n:02d}", f"เลือกแผนที่มีต้นทุนรวมต่ำสุดในมูลค่าปัจจุบัน (อัตราคิดลด {r:.0%} ต่อปี ระยะเวลา {yrs} ปี ค่าใช้จ่ายรายปีจ่ายปลายปี)",
        [f"แผน {'XYZ'[i]}: จ่ายตอนเริ่ม {fmt(u)} บาท และค่าใช้จ่ายรายปี {fmt(a)} บาท" for i, (u, a) in enumerate(plans)], pv.index(min(pv)), f"PV={[round(x) for x in pv]}")

# 2) ส่วนผสมการผลิต: ตัวลวงคือส่วนผสมที่กำไรสูงกว่าแต่ทำไม่ได้ (เกินข้อจำกัด)
n = 0
while n < 3:
    a, b = R.randrange(40, 120, 5), R.randrange(40, 120, 5)
    h1, h2, l1, l2 = R.randint(2, 6), R.randint(2, 6), R.randint(1, 5), R.randint(1, 5)
    H, Lb = R.randrange(60, 140, 10), R.randrange(50, 120, 10)
    feas = lambda x, y: h1 * x + h2 * y <= H and l1 * x + l2 * y <= Lb
    pts = [(x, y) for x in range(0, 60) for y in range(0, 60) if feas(x, y)]
    prof = lambda p: a * p[0] + b * p[1]
    best = max(pts, key=prof)
    if sum(prof(p) == prof(best) for p in pts) != 1:
        continue
    inf = [(x, y) for x in range(0, 70) for y in range(0, 70) if not feas(x, y) and prof((x, y)) > prof(best) and abs(x - best[0]) + abs(y - best[1]) <= 4]
    sub = [p for p in pts if 0.85 * prof(best) < prof(p) < 0.97 * prof(best)]
    if not inf or not sub:
        continue
    bad, low = R.choice(inf), R.choice(sub)
    n += 1
    add(f"H{3 + n:02d}", f"โรงงานผลิตสินค้า P (กำไร {a} บาท/ชิ้น ใช้เครื่อง {h1} ชม. แรงงาน {l1} ชม.) และ Q (กำไร {b} บาท/ชิ้น ใช้เครื่อง {h2} ชม. แรงงาน {l2} ชม.) ต่อสัปดาห์มีเครื่อง {H} ชม. แรงงาน {Lb} ชม. เลือกแผนผลิตที่ทำได้จริงและกำไรสูงสุด",
        [f"ผลิต P {best[0]} ชิ้น Q {best[1]} ชิ้น", f"ผลิต P {bad[0]} ชิ้น Q {bad[1]} ชิ้น", f"ผลิต P {low[0]} ชิ้น Q {low[1]} ชิ้น"], 0, f"best={best} profit={prof(best)}")

# 3) มูลค่าคาดหมาย (สองขั้นตอน)
n = 0
while n < 3:
    p, g, lo = R.choice([0.3, 0.4, 0.45, 0.5]), R.randrange(300, 900, 50) * 1000, R.randrange(100, 400, 50) * 1000
    c, q, p2, g2, l2 = R.randrange(20, 80, 10) * 1000, R.choice([0.5, 0.6, 0.7]), R.choice([0.5, 0.6, 0.7]), R.randrange(500, 1200, 100) * 1000, R.randrange(100, 400, 50) * 1000
    s = R.randrange(40, 150, 10) * 1000
    ev = [p * g - (1 - p) * lo, -c + q * (p2 * g2 - (1 - p2) * l2), s]
    srt = sorted(ev, reverse=True)
    if srt[0] - srt[1] < 0.06 * abs(srt[0]) or ev.index(max(ev)) == 2 and n < 2:
        continue
    n += 1
    add(f"H{6 + n:02d}", f"เลือกทางที่มูลค่าคาดหมายสูงสุด (เป็นเงินบาท)",
        [f"ลงทุนเต็มทันที: สำเร็จด้วยความน่าจะเป็น {p:.0%} ได้กำไร {fmt(g)} ถ้าล้มเหลวขาดทุน {fmt(lo)}",
         f"ทดลองนำร่องก่อน: ค่านำร่อง {fmt(c)} โอกาสนำร่องผ่าน {q:.0%} ถ้าผ่านลงทุนเต็ม (สำเร็จ {p2:.0%} ได้กำไร {fmt(g2)} ล้มเหลวขาดทุน {fmt(l2)}) ถ้าไม่ผ่านหยุดและเสียแค่ค่านำร่อง",
         f"ไม่ลงทุน รับผลตอบแทนแน่นอน {fmt(s)}"], ev.index(max(ev)), f"EV={[round(x) for x in ev]}")

# 4) เส้นทางวิกฤต: ลดเวลางานไหนแล้วโครงการสั้นลงมากสุด
def cpm(d):
    es = {"A": 0}
    es["B"] = d["A"]; es["C"] = d["A"]; es["D"] = es["B"] + d["B"]; es["E"] = es["C"] + d["C"]
    es["F"] = max(es["D"] + d["D"], es["E"] + d["E"]); return es["F"] + d["F"]
n = 0
while n < 3:
    d = {k: R.randint(3, 10) for k in "ABCDEF"}; base = cpm(d)
    gain = {}
    for k in "ABCDEF":
        d2 = dict(d); d2[k] = max(1, d[k] - 3); gain[k] = base - cpm(d2)
    cand = R.sample("ABCDEF", 3); gs = sorted((gain[k] for k in cand), reverse=True)
    if gs[0] == gs[1] or gs[0] == 0:
        continue
    n += 1
    top = max(cand, key=lambda k: gain[k])
    add(f"H{9 + n:02d}", f"โครงการมีงาน A–F: A ต้องเสร็จก่อน B และ C · B ก่อน D · C ก่อน E · D และ E ต้องเสร็จก่อน F (ทำคู่ขนานได้เมื่อไม่ขึ้นต่อกัน) ระยะเวลา (วัน): " + " ".join(f"{k}={d[k]}" for k in "ABCDEF") + " มีงบลดเวลาได้ 1 งาน ลงได้ 3 วัน (ไม่ต่ำกว่า 1 วัน) เลือกงานที่ทำให้โครงการเสร็จเร็วขึ้นมากที่สุด",
        [f"ลดเวลางาน {k}" for k in cand], cand.index(top), f"base={base} gain={gain}")

# 5) ปริมาณสั่งซื้อ: เลือกต้นทุนรวมรายปีต่ำสุด
n = 0
while n < 2:
    D, S, H = R.randrange(2000, 9000, 500), R.randrange(100, 600, 50), R.randrange(2, 9)
    q = math.sqrt(2 * D * S / H)
    qs = sorted({int(round(q * f, -1)) or 10 for f in (0.55, 0.8, 1.35, 1.7)})
    cand = R.sample(qs, 3); tc = [D / x * S + x / 2 * H for x in cand]; srt = sorted(tc)
    if srt[1] / srt[0] < 1.04:
        continue
    n += 1
    add(f"H{12 + n:02d}", f"ความต้องการต่อปี {D:,} ชิ้น ค่าสั่งซื้อครั้งละ {S} บาท ค่าเก็บรักษา {H} บาท/ชิ้น/ปี เลือกปริมาณสั่งต่อครั้งที่ต้นทุนรวมต่อปี (ค่าสั่ง+ค่าเก็บ) ต่ำสุด",
        [f"สั่งครั้งละ {x:,} ชิ้น" for x in cand], tc.index(min(tc)), f"EOQ={q:.0f} TC={[round(x) for x in tc]}")

# 6) จัดคนเข้างานตามเงื่อนไข: ตัวเลือกลวงฝ่าฝืนเงื่อนไขเพียงข้อเดียว
people, jobs = ["สมชาย", "มานี", "ปิติ", "วีณา"], [1, 2, 3, 4]
n = 0
while n < 2:
    cons = []
    for _ in range(R.randint(5, 6)):
        k = R.choice(["not", "eq", "lt"]); p, q_ = R.sample(range(4), 2); j = R.randint(1, 4)
        cons.append((k, p, q_, j))
    def ok(perm, c):
        k, p, q_, j = c
        return perm[p] != j if k == "not" else perm[p] == j if k == "eq" else perm[p] < perm[q_]
    def txt(c):
        k, p, q_, j = c
        return f"{people[p]}ไม่ได้รับงานที่ {j}" if k == "not" else f"{people[p]}ได้รับงานที่ {j}" if k == "eq" else f"งานของ{people[p]}มีเลขน้อยกว่างานของ{people[q_]}"
    perms = list(itertools.permutations(jobs))
    good = [pm for pm in perms if all(ok(pm, c) for c in cons)]
    if len(good) != 1:
        continue
    near = [pm for pm in perms if sum(not ok(pm, c) for c in cons) == 1]
    if len(near) < 2:
        continue
    n += 1; wrong = R.sample(near, 2)
    show = lambda pm: " ".join(f"{people[i]}→งาน{pm[i]}" for i in range(4))
    add(f"H{14 + n:02d}", f"จัดคน 4 คนเข้างาน 4 งาน (คนละงาน ไม่ซ้ำ) ตามเงื่อนไขทั้งหมด: " + "; ".join(txt(c) for c in cons) + " เลือกการจัดที่ทำตามเงื่อนไขครบทุกข้อ",
        [show(good[0]), show(wrong[0]), show(wrong[1])], 0, f"unique={good[0]}")

assert len(tasks) == 16, len(tasks)
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hard_tasks.json")
json.dump(tasks, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from collections import Counter
print(len(tasks), "ข้อ · เฉลย:", dict(Counter(t["answer"] for t in tasks)))
