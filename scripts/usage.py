# -*- coding: utf-8 -*-
"""สถิติการใช้ AI: ทีม (จากตาราง audit) + Claude (นับโทเคนจาก transcript ในเครื่อง) — อ่านเฉพาะตัวเลข ไม่เก็บเนื้อหาบทสนทนา"""
import glob, io, json, os, time, datetime
from collections import defaultdict

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".ai-team", "claude_usage_cache.json")
_mem = {"t": 0, "v": None}


def day(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def scan_file(path):
    """คืน {msgid: [model, day, in, out, cache_read, cache_create]} ของไฟล์เดียว (dedupe ตาม message id)"""
    out = {}
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message")
            if d.get("type") != "assistant" or not isinstance(m, dict) or not m.get("usage"):
                continue
            u = m["usage"]
            model = m.get("model") or "unknown"
            if model.startswith("<"):
                continue
            ts = d.get("timestamp") or ""
            try:
                dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).astimezone()
                dd = dt.strftime("%Y-%m-%d")
            except ValueError:
                dd = ts[:10]
            key = m.get("id") or d.get("uuid") or ts
            out[key] = [model, dd, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                        int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)]
    return out


def claude_usage(days=14):
    try:
        cache = json.load(io.open(CACHE, encoding="utf-8"))
    except (IOError, ValueError):
        cache = {}
    files = glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl"))
    new = {}
    for p in files:
        mt = os.path.getmtime(p)
        c = cache.get(p)
        if c and c["mt"] == mt:
            new[p] = c
        else:
            new[p] = {"mt": mt, "rows": scan_file(p)}
    try:
        io.open(CACHE, "w", encoding="utf-8").write(json.dumps(new, ensure_ascii=False))
        os.chmod(CACHE, 0o600)
    except (IOError, OSError):
        pass
    seen, models, per_day = set(), defaultdict(lambda: [0, 0, 0, 0, 0]), defaultdict(lambda: defaultdict(int))
    for p, c in new.items():
        for k, (model, dd, i, o, cr, cc) in c["rows"].items():
            if (p, k) in seen:
                continue
            seen.add((p, k))
            r = models[model]
            r[0] += 1; r[1] += i; r[2] += o; r[3] += cr; r[4] += cc
            per_day[dd][model] += o + i
    cutoff = day(time.time() - days * 86400)
    return dict(
        models=sorted(({"model": m, "calls": r[0], "input": r[1], "output": r[2], "cache_read": r[3], "cache_create": r[4]} for m, r in models.items()),
                      key=lambda x: -(x["output"] + x["input"])),
        days=[{"d": d, "by": dict(v)} for d, v in sorted(per_day.items()) if d >= cutoff],
        sessions=len(files))


def team_usage(c, days=14):
    gw = {}
    for seat, g in c.execute("select s.name,k.gateway from seats s join catalog k on k.name=s.catalog"):
        gw[seat] = g
    by = defaultdict(lambda: dict(calls=0, errors=0, blocked=0, ms=0, msn=0, chars_out=0, chars_in=0, last=0, model=""))
    per_day = defaultdict(lambda: defaultdict(int))
    jobs = defaultdict(int)
    for t, agent, model, brief, reply, blocked, job, ms in c.execute("select t,agent,model,brief,reply,blocked,job,ms from audit"):
        r = by[agent]
        r["calls"] += 1
        r["model"] = model or r["model"]
        r["last"] = max(r["last"], t)
        r["chars_in"] += len(brief or "")
        r["chars_out"] += len(reply or "")
        if blocked is not None:
            r["errors" if str(blocked).startswith("error:") else "blocked"] += 1
        if ms:
            r["ms"] += ms; r["msn"] += 1
        per_day[day(t)][agent] += 1
        jobs[job or "—"] += 1
    cutoff = day(time.time() - days * 86400)
    seats = []
    for a, r in by.items():
        seats.append(dict(agent=a, model=r["model"], gateway=gw.get(a, "—"), calls=r["calls"], errors=r["errors"], blocked=r["blocked"],
                          avg_s=round(r["ms"] / r["msn"] / 1000, 1) if r["msn"] else None, chars_in=r["chars_in"], chars_out=r["chars_out"], last=r["last"]))
    seats.sort(key=lambda x: -x["calls"])
    return dict(seats=seats, days=[{"d": d, "by": dict(v)} for d, v in sorted(per_day.items()) if d >= cutoff],
                top_jobs=sorted(({"job": j, "calls": n} for j, n in jobs.items()), key=lambda x: -x["calls"])[:8])


def all_usage(c):
    if time.time() - _mem["t"] < 20 and _mem["v"]:
        return _mem["v"]
    plan = c.execute("select v from settings where k='claude_plan'").fetchone()
    v = dict(team=team_usage(c), claude=claude_usage(), plan=json.loads(plan[0]) if plan else None, at=time.time())
    _mem.update(t=time.time(), v=v)
    return v
