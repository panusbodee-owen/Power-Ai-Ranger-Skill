#!/usr/bin/env python3
"""สะพานให้ลูกทีมใช้ Mac เสมือน (terminal-mac)
ใช้: python3 bridge.py SEAT "เป้าหมาย" [--steps 15] [--vm default]
วนรอบ: ถ่ายจอ -> OCR เป็นข้อความ+พิกัด -> ส่งให้ลูกทีม -> ลูกทีมตอบ action JSON 1 อัน -> สั่งเครื่องเสมือน
ปลอดภัย: สั่งได้เฉพาะในเครื่องเสมือน (ไม่แตะเครื่องจริง), จำกัดจำนวนรอบ, ห้ามพิมพ์รหัสผ่าน (เช็กคำต้องห้าม)
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TM = os.path.expanduser("~/.local/bin/terminal-mac")
TEAM = os.path.join(HERE, "..", "team.py")  # scripts/team.py
OUT = os.path.join(HERE, "runs")
ALLOWED = {"click", "double", "right", "type", "key", "scroll", "wait", "done"}
BLOCK = re.compile(r"password|passwd|รหัสผ่าน|sudo|api[_-]?key|token", re.I)

PROMPT = """You control a macOS virtual machine (sandbox, 1280x800 points) for a test task.
GOAL: {goal}
You cannot see images. Below is the screen as OCR text lines: "x,y|text" where x,y is the CENTER of that text.
Click on text by using its x,y. To replace a field value: click it, key cmd+a, then type. Input boxes are often right next to or under their label/placeholder text.
Previous actions and results:
{history}
CURRENT SCREEN (step {step}/{steps}):
{screen}
Reply with exactly ONE JSON object and nothing else, one of:
{{"action":"click","x":N,"y":N}} | {{"action":"double","x":N,"y":N}} | {{"action":"right","x":N,"y":N}}
{{"action":"type","text":"..."}} (types into the focused field) | {{"action":"key","keys":"cmd+l"}} (e.g. return, tab, cmd+a, cmd+space)
{{"action":"scroll","dy":-400}} (negative = scroll down; scrolls the page, safe for number fields) | {{"action":"wait"}}
{{"action":"done","result":"what you found / pass or fail and why"}}
Add "why":"short reason" to every action."""


def tm(*a, timeout=60):
    r = subprocess.run([TM, "action", *a], capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()


def screen(path):
    tm("screenshot", "--output", path)
    r = subprocess.run([os.path.join(HERE, "ocr"), path], capture_output=True, text=True, timeout=60)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    lines.sort(key=lambda l: (int(l.split(",")[1].split("|")[0]) // 12, int(l.split(",")[0])))
    return "\n".join(lines[:160])


def ask(seat, brief, job):
    r = subprocess.run([sys.executable, TEAM, "ask", "--job", job, seat, brief],
                       capture_output=True, text=True, timeout=300)
    return r.stdout + r.stderr


def parse(txt):
    for m in re.finditer(r"\{[^{}]*\}", txt, re.S):
        try:
            a = json.loads(m.group(0))
            if a.get("action") in ALLOWED:
                return a
        except Exception:
            pass
    return None


def run_action(a):
    k = a["action"]
    if k in ("click", "double", "right"):
        x, y = int(a["x"]), int(a["y"])
        if not (0 <= x <= 1280 and 0 <= y <= 800):
            return "rejected: out of screen"
        extra = ["--double"] if k == "double" else (["--button", "right"] if k == "right" else [])
        tm("click", str(x), str(y), *extra)
        return f"clicked {x},{y}"
    if k == "type":
        t = str(a.get("text", ""))[:500]
        if BLOCK.search(t):
            return "rejected: looks like a secret"
        tm("type", t)
        return f"typed {t!r}"
    if k == "key":
        tm("key", str(a.get("keys", ""))[:40])
        return f"pressed {a.get('keys')}"
    if k == "scroll":
        tm("click", "1150", "400")  # โฟกัสขอบหน้า ไม่ให้ล้อเลื่อนไปเปลี่ยนค่าช่องตัวเลข
        tm("scroll", "--dy", str(int(a.get("dy", -400))), "--x", "1150", "--y", "400")
        return "scrolled"
    if k == "wait":
        time.sleep(3)
        return "waited"
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seat"); ap.add_argument("goal")
    ap.add_argument("--steps", type=int, default=15)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    run = time.strftime("%Y%m%d-%H%M%S"); rdir = os.path.join(OUT, run); os.makedirs(rdir)
    job = f"VM {a.seat} {run}"
    hist, log = [], {"seat": a.seat, "goal": a.goal, "steps": []}
    for i in range(1, a.steps + 1):
        shot = os.path.join(rdir, f"{i:02d}.png")
        scr = screen(shot)
        brief = PROMPT.format(goal=a.goal, history="\n".join(hist[-8:]) or "(none)", step=i, steps=a.steps, screen=scr)
        if len(brief) > 7800:
            brief = brief[:7800]
        reply = ask(a.seat, brief, job)
        act = parse(reply)
        if act is None:
            res = "no valid JSON action in reply"
            if "[BLOCKED]" in reply:
                res = "team call blocked: " + reply.strip()[:120]
                log["steps"].append({"step": i, "reply": reply[-400:], "result": res}); print(i, res); break
        elif act["action"] == "done":
            log["result"] = act.get("result", ""); log["steps"].append({"step": i, "action": act}); print(i, "DONE:", act.get("result")); break
        else:
            res = run_action(act); time.sleep(1.5)
        hist.append(f"{i}. {json.dumps(act, ensure_ascii=False) if act else reply.strip()[:120]} -> {res}")
        log["steps"].append({"step": i, "action": act, "result": res}); print(i, act, "->", res, flush=True)
    screen(os.path.join(rdir, "final.png"))
    json.dump(log, open(os.path.join(rdir, "log.json"), "w"), ensure_ascii=False, indent=1)
    print("log:", rdir)


if __name__ == "__main__":
    main()
