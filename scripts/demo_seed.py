#!/usr/bin/env python3
"""สร้างข้อมูลเดโม (สมมติทั้งหมด ไม่มี key/โฮสต์จริง) สำหรับถ่ายภาพหน้าจอ/GIF
ใช้: AI_TEAM_DB=/tmp/ai-team-demo.db python3 demo_seed.py base|working|partial|done
แล้วเปิดแดชบอร์ดด้วย AI_TEAM_DB เดียวกัน (AI_TEAM_PORT=8791 python3 dash.py)"""
import os, sys, time
os.environ.setdefault("AI_TEAM_DB", "/tmp/ai-team-demo.db")
from team import db, DB

stage = sys.argv[1] if len(sys.argv) > 1 else "base"
if "demo" not in os.path.basename(DB):
    sys.exit("ปฏิเสธ: ชื่อไฟล์ DB ต้องมีคำว่า demo (กันเขียนทับ DB จริง)")
if stage == "base" and os.path.exists(DB):
    os.remove(DB)
c = db()
now = time.time()
JOB = "Review lease clause 4.2 (vote)"
SEATS = ["worker", "reviewer", "intern"]


def row(t, agent, model, brief, reply, job, ms, blocked=None):
    c.execute("insert into audit(t,agent,model,brief,reply,blocked,job,ms) values(?,?,?,?,?,?,?,?)", (t, agent, model, brief, reply, blocked, job, ms))


if stage == "base":
    c.execute("insert or replace into gateways values('gateway-a','https://gateway-a.example.com/v1','demo')")
    c.execute("insert or replace into gateways values('gateway-b','https://gateway-b.example.com/v1','demo')")
    cat = [("fast-coder-v4", "gateway-a", "vendor-a/fast-coder-v4", 0.15, 0.60, "ok"), ("reasoner-120b", "gateway-b", "vendor-b/reasoner-120b", 0.40, 1.60, "ok"),
           ("nano-30b", "gateway-b", "vendor-b/nano-30b", 0.05, 0.20, "ok"), ("mega-550b", "gateway-b", "vendor-b/mega-550b", 1.20, 4.80, "ok"),
           ("legacy-70b", "gateway-b", "vendor-b/legacy-70b", None, None, "fail"), ("tiny-e4b", "gateway-b", "vendor-b/tiny-e4b", 0.02, 0.08, None)]
    c.executemany("insert or replace into catalog values(?,?,?,?,?,?)", cat)
    c.execute("delete from seats")
    for n, t, r, k in [("worker", "Worker", "Fast, accurate, concise.", "fast-coder-v4"), ("reviewer", "Reviewer", "Check claims carefully.", "reasoner-120b"),
                       ("intern", "Intern", "Explain step by step.", "nano-30b"), ("seat4", "Seat 4", "", None)]:
        c.execute("insert into seats values(?,?,?,?)", (n, t, r, k))
    c.execute("insert or replace into settings values('n','3')"); c.execute("insert or replace into settings values('mode','vote')")
    M = {"worker": "vendor-a/fast-coder-v4", "reviewer": "vendor-b/reasoner-120b", "intern": "vendor-b/nano-30b"}
    for i, (job, brief, note) in enumerate([
            ("Draft unit tests for parse_date()", "Write 5 pytest cases for parse_date(); code only.", "Checked: all 5 cases run and pass. Worker's edge case for leap day was the best."),
            ("Summarize Q3 support tickets", "Summarize the 12 ticket titles below into 3 themes.", "Checked: themes match the data; intern missed the billing theme, worker and reviewer agree.")]):
        t0 = now - 1500 + i * 420
        for j, s in enumerate(["worker", "reviewer"]):
            row(t0 + j * 3, s, M[s], brief, "Done. (demo output)", job, 2200 + 900 * j + 300 * i)
        c.execute("insert into notes values(?,?,?)", (t0 + 40, job, note))
    row(now - 200, "intern", M["intern"], "Explain what a pytest fixture is in 2 lines.", "A fixture sets up shared test data. (demo output)", "Practice: pytest basics", 1800)
elif stage in ("working", "partial", "done"):
    M = {"worker": "vendor-a/fast-coder-v4", "reviewer": "vendor-b/reasoner-120b", "intern": "vendor-b/nano-30b"}
    brief = "Clause 4.2 says the lease renews automatically unless notice is given 60 days before end. Is this an automatic-renewal clause? First line: yes or no, then one sentence of reasoning."
    answers = {"worker": ("yes\nIt renews by default unless notice is given.", 2600), "reviewer": ("yes\nSilence by the tenant results in renewal.", 4100), "intern": ("no\nIt only mentions notice, not renewal.", 1900)}
    c.execute("delete from audit where job=?", (JOB,)); c.execute("delete from notes where job=?", (JOB,))
    t0 = now - 6
    for s in SEATS:
        if stage == "working" or (stage == "partial" and s != "worker"):
            row(t0, s, M[s], brief, None, JOB, None)
        else:
            row(t0, s, M[s], brief, answers[s][0], JOB, answers[s][1])
    if stage == "done":
        c.execute("insert into notes values(?,?,?)", (now, JOB, "Vote: yes x2, no x1 -> yes. Intern's 'no' is wrong: the clause renews when no notice is given, which is automatic renewal. Verified against the clause text."))
c.commit()
print("stage:", stage, "db:", DB)
