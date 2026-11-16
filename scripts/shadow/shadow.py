# -*- coding: utf-8 -*-
"""โหมด shadow: ให้ทีมฟรีกับ Claude ทำโจทย์ชุดเดียวกัน แล้วตรวจด้วยเฉลยแบบโปรแกรม (ไม่ใช้ AI ตัดสิน → ไม่มีอคติรูปแบบคำตอบ)

  python3 shadow.py free   TASKS --job shadow1 --out free.json            # ส่งทีมฟรี (pool @auto ระดับ heavy ตามที่ตัวตัดสินใช้จริง) แล้วเก็บคำตอบ
  python3 shadow.py score  TASKS --free free.json --claude sonnet=s.json --claude opus=o.json [--record]

ไฟล์คำตอบเป็น {"T01": "<ข้อความตอบดิบ>", ...}  (Claude ให้ subagent ตอบ JSON รูปแบบเดียวกัน)
เกณฑ์ (กำหนดก่อนรัน): ทีมฟรี "เทียบเท่า" เมื่อเลือกถูก >= (ของ Sonnet - 1 ข้อ) · คะแนนคำสำคัญเป็นตัวรอง
--record: บันทึกผลลง decisions (outcome=pass เมื่อเลือกถูก) ให้เลนเรียนรู้จากผลจริง
"""
import argparse, json, os, re, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import team, routing  # noqa: E402


def load(p):
    return json.load(open(p, encoding="utf-8"))


def cmd_free(a):
    tasks = load(a.tasks); c = team.db(); ids = {}; prev = {}
    if a.fill_missing and os.path.exists(a.out):  # รันซ้ำเฉพาะข้อที่ยังไม่มีคำตอบ (เช่น ติดเพดานครั้ง/ชั่วโมง หรือ 429) ข้อที่ตอบแล้วคงไว้
        prev = load(a.out); tasks = [t for t in tasks if not (prev.get(t["id"]) or "").strip()]
        print(f"ทำซ้ำเฉพาะที่ยังไม่มีคำตอบ {len(tasks)} ข้อ")
    for t in tasks:
        ids[t["id"]] = team.submit(c, t["brief"], a.pool, job=a.job, chk=None, tier="heavy")
    print(f"ส่งทีมฟรี {len(ids)} งาน (job={a.job}, pool={a.pool}, tier=heavy)")
    team.worker(8, 3, drain=True)
    out, meta = dict(prev), {}
    for tid, task_id in ids.items():
        r = c.execute("select status,used_seat,result,error from tasks where id=?", (task_id,)).fetchone()
        out[tid] = r[2] or ""; meta[tid] = {"status": r[0], "seat": r[1], "error": r[3]}
    tok = c.execute("select count(*), coalesce(sum(in_tok),0), coalesce(sum(out_tok),0), coalesce(sum(reason_tok),0) from spend where job=? and family='team'", (a.job,)).fetchone()
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"meta": meta, "tokens": {"calls": tok[0], "in": tok[1], "out": tok[2], "reasoning": tok[3]}}, open(a.out + ".meta", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"เสร็จ: {sum(1 for m in meta.values() if m['status'] == 'done')}/{len(ids)} · โทเคนทีมฟรี {tok[1]:,} เข้า / {tok[2]:,} ออก (คิด {tok[3]:,}) → {a.out}")


def grade(task, text):
    obj = team.first_json(text or "", dict) or {}
    choice = str(obj.get("choice", "")).strip().upper()[:1]
    body = (str(obj.get("reason", "")) + " " + (text or "")) if obj else (text or "")
    hits = sum(bool(re.search(g, body)) for g in task["groups"])
    return choice == task["answer"], hits, bool(obj)


def cmd_score(a):
    tasks = load(a.tasks); sides = {"free": load(a.free)} if a.free else {}
    for kv in a.claude or []:
        k, p = kv.split("=", 1); sides[k] = load(p)
    res = {k: {t["id"]: grade(t, v.get(t["id"], "")) for t in tasks} for k, v in sides.items()}
    n = len(tasks); ng = sum(len(t["groups"]) for t in tasks)
    print(f"{'ผู้ทำ':<8} {'เลือกถูก':>8} {'คำสำคัญ':>10} {'JSON ผิดรูปแบบ':>14}")
    for k, r in res.items():
        print(f"{k:<8} {sum(x[0] for x in r.values()):>5}/{n:<3} {sum(x[1] for x in r.values()):>6}/{ng:<3} {sum(not x[2] for x in r.values()):>10}")
    print("\nข้อที่ผู้ทำต่างกัน (✓ถูก ✗ผิด):")
    ks = list(res)
    for t in tasks:
        row = [res[k][t["id"]][0] for k in ks]
        if len(set(row)) > 1:
            print(f"  {t['id']} เฉลย {t['answer']}: " + " ".join(f"{k}{'✓' if res[k][t['id']][0] else '✗'}" for k in ks))
    if "sonnet" in res:
        sv = sum(x[0] for x in res["sonnet"].values())
        print(f"\nเกณฑ์ที่กำหนดไว้ก่อนรัน: ผู้ทำฟรีเทียบเท่า Sonnet เมื่อเลือกถูก >= Sonnet({sv}) - 1 (n={n}: 1 ข้อ = {100 / n:.0f} จุด %)")
        for k, r in res.items():
            if k not in ("sonnet", "opus"):
                f = sum(x[0] for x in r.values())
                print(f"  {k:<8} {f}/{n} → " + ("เทียบเท่า" if f >= sv - 1 else "ไม่เทียบเท่า"))
    if a.save:  # เก็บผลต่อผู้ทำลง shadow_results ให้แท็บ Ranking/Routing ในแดชบอร์ดใช้
        c = team.db(); routing.ensure(c)
        for k, r in res.items():
            c.execute("insert into shadow_results(t,set_name,side,correct,total,answered) values(?,?,?,?,?,?)",
                      (time.time(), a.save, k, sum(x[0] for x in r.values()), n, sum(x[2] for x in r.values())))
        c.commit(); print(f"บันทึกผลลง shadow_results (ชุด {a.save}) แล้ว")
    if a.record:
        c = team.db(); routing.ensure(c)
        for k, r in res.items():
            ex = "team" if k == "free" else "subagent:" + k
            for t in tasks:
                lane = routing.profile(t["brief"])["lane"]
                c.execute("insert into decisions(t,job,lane,tier,executor,stakes,needs_tools,reason,outcome) values(?,?,?,?,?,?,?,?,?)",
                          (time.time(), "shadow", lane, "heavy", ex, 0, 0, "shadow " + t["id"], "pass" if r[t["id"]][0] else "fail"))
        c.commit(); print("บันทึกผลลง decisions แล้ว (ดู: team.py lanes)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("free"); p.add_argument("tasks"); p.add_argument("--job", default="shadow1"); p.add_argument("--pool", default="@auto"); p.add_argument("--out", required=True); p.add_argument("--fill-missing", action="store_true")
    p = sp.add_parser("score"); p.add_argument("tasks"); p.add_argument("--free"); p.add_argument("--claude", action="append"); p.add_argument("--record", action="store_true"); p.add_argument("--save", help="ชื่อชุด (เก็บผลต่อผู้ทำลง shadow_results)")
    a = ap.parse_args()
    {"free": cmd_free, "score": cmd_score}[a.cmd](a)
