# -*- coding: utf-8 -*-
"""ข้อมูลสำหรับแท็บ Ranking และ Routing ของแดชบอร์ด (อ่านอย่างเดียว ไม่เลือก api_key ออกมา)

Ranking = จัดอันดับช่อง AI ในทีมว่า "ฝีมือดีและพลาดน้อย" แค่ไหน จากหลักฐานจริงในฐานข้อมูล:
  คุณภาพ  = ค่าเฉลี่ยของหลักฐานที่มี (งานจริงที่มีตัวเช็ก, ผล shadow, คะแนนความถนัด) แต่ละอย่างปรับ Laplace (k+1)/(n+2) กันคะแนนเต็มจากตัวอย่างน้อย
  ความน่าเชื่อถือ = สัดส่วนครั้งที่เรียกสำเร็จ (ไม่นับถูกบล็อกด้วยเพดาน/ตัวกัน เพราะไม่ใช่ความผิดของช่อง) ปรับ Laplace เช่นกัน
  คะแนนรวม = คุณภาพ x ความน่าเชื่อถือ  (ช่องที่ไม่มีหลักฐานคุณภาพเลย = "ยังไม่วัด" ไม่ได้ลำดับ)
  หลักฐานน้อย (< 10 รายการ) ติดป้ายเตือนไว้ ไม่ปิดบังว่าลำดับยังไม่นิ่ง"""
import json, time
import routing

MIN_EVIDENCE = 10


def _lap(k, n):
    return (k + 1.0) / (n + 2.0)


def _skill_rows(c, seat):
    return [(sk, sc, nt) for sk, sc, nt in c.execute("select skill,score,coalesce(note,'') from seat_skills where seat=? order by skill", (seat,))]


def seat_stats(c, team):
    """ตัวชี้วัดต่อช่องที่มีโมเดล"""
    out = []
    shadow = {}
    for sd, side, k, tot, ans, t in c.execute("select set_name,side,correct,total,answered,t from shadow_results order by t"):
        shadow[(sd, side)] = (k, tot, ans)  # ใช้ผลล่าสุดของแต่ละชุด
    rows = c.execute("""select s.name,s.title,k.model,g.name,g.base_url from seats s join catalog k on k.name=s.catalog join gateways g on g.name=k.gateway order by s.rowid""").fetchall()
    seat_names = {r[0] for r in rows}
    for name, title, model, gw, url in rows:
        ok, err, blk, trunc = c.execute("""select coalesce(sum(blocked is null and reply is not null),0),
            coalesce(sum(blocked like 'error:%' and blocked not like 'error: truncated%'),0), coalesce(sum(blocked is not null and blocked not like 'error:%'),0),
            coalesce(sum(blocked like 'error: truncated%'),0) from audit where agent=?""", (name,)).fetchone()
        ms = c.execute("select avg(ms) from (select ms from audit where agent=? and ms is not null and blocked is null order by rowid desc limit 40)", (name,)).fetchone()[0]
        tk_pass, tk_fail = c.execute("select coalesce(sum(status='done' and chk is not null),0), coalesce(sum(status in ('check_failed','rejected')),0) from tasks where used_seat=?", (name,)).fetchone()
        sp = c.execute("select count(*), coalesce(avg(out_tok),0), coalesce(sum(reason_tok),0), coalesce(sum(case when reason_tok is not null then out_tok end),0) from spend where seat=? and family='team' and state='settled'", (name,)).fetchone()
        skills = _skill_rows(c, name)
        ev = []  # (ชื่อหลักฐาน, คะแนน 0-1, จำนวน, ข้อที่ถูก) — คุณภาพรวมถ่วงน้ำหนักตามจำนวน: (Σถูก+1)/(Σทั้งหมด+2)
        if tk_pass + tk_fail:
            ev.append(("งานจริงที่มีตัวเช็ก", _lap(tk_pass, tk_pass + tk_fail), tk_pass + tk_fail, tk_pass))
        for (sd, side), (k, tot, ans) in shadow.items():
            if side == name:
                if sum(1 for (s2, sd2) in shadow if s2 == sd and sd2 in seat_names) >= 3:  # นับเฉพาะชุดที่วัดช่องในทีม >= 3 ช่อง (เทียบกันได้) กันช่องที่ถูกวัดชุดเดียวคนเดียวได้เปรียบ
                    ev.append((f"shadow:{sd}", _lap(k, tot), tot, k))
        if skills and not ev:  # คะแนนความถนัดบางตัวมาจากผล shadow เดียวกัน: ใช้เป็นหลักฐานเฉพาะเมื่อไม่มีหลักฐานอื่น กันนับซ้ำ
            m = sum(x[1] for x in skills) / len(skills)
            ev.append(("คะแนนความถนัด (ตั้งเอง/วัดไว้ก่อน)", m, 4, m * 4))  # น้ำหนักเท่าตัวอย่าง 4 รายการ: เป็นแค่ค่าตั้งต้น
        quality = (sum(e[3] for e in ev) + 1.0) / (sum(e[2] for e in ev) + 2.0) if ev else None
        rel = _lap(ok, ok + err)
        out.append(dict(seat=name, title=title, model=model, gateway=gw, cli=str(url).startswith("cli://"),
                        calls_ok=ok, errors=err, blocked=blk, truncated=trunc, reliability=rel, avg_ms=ms,
                        task_pass=tk_pass, task_fail=tk_fail, avg_out_tok=sp[1], reason_share=(sp[2] / sp[3]) if sp[3] else None, spend_calls=sp[0],
                        skills=[dict(skill=a, score=b, note=n_) for a, b, n_ in skills], evidence=[dict(name=a, score=b, n=n_) for a, b, n_, _k in ev],
                        evidence_n=sum(e[2] for e in ev) + ok + err, quality=quality,
                        score=(quality * rel) if quality is not None else None, cooling=routing.cooling(c, name)))
    ranked = sorted([x for x in out if x["score"] is not None], key=lambda x: (-round(x["score"], 2), x["avg_ms"] or 9e9))
    for i, x in enumerate(ranked, 1):
        x["rank"] = i
        x["thin"] = x["evidence_n"] < MIN_EVIDENCE
    unranked = [x for x in out if x["score"] is None]
    for x in unranked:
        x["rank"] = None; x["thin"] = True
    return ranked + unranked


def tier_orders(c, team):
    """ลำดับที่ระบบจะเรียกจริงตามระดับความยาก (ใช้ routing.rank ตัวเดียวกับตอนส่งงาน)"""
    import team as T
    seats = [x["seat"] for x in team]
    out = {}
    for tier in routing.TIERS:
        try:
            order = routing.rank(c, seats, tier, lambda s: T.quality(c, s), lambda s: T.avg_ms(c, s), lambda s: 0, None, None)
        except Exception:
            order = seats
        out[tier] = [dict(seat=s, score=round(T.quality(c, s), 2)) for s in order]
    return out


def ranking(c):
    import team as T
    team = seat_stats(c, T)
    return dict(seats=team, tiers=tier_orders(c, team), bars=routing.BAR, formula="คะแนนรวม = คุณภาพ × ความน่าเชื่อถือ (ปรับ Laplace) · หลักฐานน้อยกว่า %d รายการติดป้ายเตือน" % MIN_EVIDENCE)


def routing_view(c):
    now = time.time()
    out = dict(mode=routing.get(c, "route:mode"), lanes={k: routing.get(c, "exec:" + k) for k in ("action", "general", "judgment", "bulk", "top", "free_pool")},
               opus_share=routing.get(c, "route:opus_share"), learn_min=routing.get(c, "route:learn_min"), free_min_pass=routing.get(c, "route:free_min_pass"),
               escalate_after=routing.get(c, "route:escalate_after"), escalate=routing.get(c, "route:escalate"), guard=routing.get(c, "guard:enabled"))
    caps = []
    for scope, label in (("day", "วันนี้"), ("month", "เดือนนี้")):
        ct, cu = routing.caps(c, scope)
        tok, usd, unp = routing.used(c, routing._start(scope, now))
        caps.append(dict(scope=label, tokens=tok, cap_tokens=ct, usd=usd, cap_usd=cu, unpriced_tokens=unp))
    ctc = routing.num(routing.get(c, "cap:claude_day_tokens"))
    out["caps"] = caps
    out["claude_day"] = dict(tokens=routing.used(c, routing._start("day", now), None, "claude")[0], cap=ctc)
    days = []
    for d in range(13, -1, -1):
        a = routing._start("day", now - d * 86400); b = a + 86400
        days.append(dict(t=a, team=routing.used(c, a, None, "team")[0] - routing.used(c, b, None, "team")[0], claude=routing.used(c, a, None, "claude")[0] - routing.used(c, b, None, "claude")[0]))
    out["days"] = days
    dec = c.execute("select lane,executor,count(*),coalesce(sum(outcome='pass'),0),coalesce(sum(outcome='fail'),0) from decisions where coalesce(job,'')!='shadow' group by lane,executor order by lane,executor").fetchall()
    out["decisions"] = [dict(lane=a, executor=b, n=n, passed=p, failed=f) for a, b, n, p, f in dec]
    tot = sum(x["n"] for x in out["decisions"]); team_n = sum(x["n"] for x in out["decisions"] if x["executor"] == "team")
    out["share"] = dict(total=tot, team=team_n, claude=tot - team_n)
    out["recent"] = [dict(id=i, t=t, job=j, lane=l, tier=ti, executor=e, stakes=bool(st), tools=bool(nt), reason=r, outcome=o) for i, t, j, l, ti, e, st, nt, r, o in
                     c.execute("select id,t,job,lane,tier,executor,stakes,needs_tools,reason,outcome from decisions where coalesce(job,'')!='shadow' order by id desc limit 15")]
    out["learned"] = []
    for lane in ("action", "general", "judgment", "bulk"):
        st = routing.lane_stats(c, lane)
        if st:
            out["learned"].append(dict(lane=lane, n=st[0], rate=st[1], skip=st[0] >= int(out["learn_min"] or 5) and st[1] < float(out["free_min_pass"] or 0.6)))
    out["tier_cost"] = [dict(tier=t or "(ไม่ระบุ)", n=n, tokens=tok) for t, n, tok in c.execute(
        "select tier,count(distinct task_id),coalesce(sum(coalesce(in_tok,est_in)+coalesce(out_tok,est_out)),0) from spend where family='team' and task_id is not null group by tier order by tier")]
    out["escalated"] = c.execute("select count(*) from tasks where coalesce(escalated,0)>0").fetchone()[0]
    out["shadow"] = [dict(set=sd, side=side, correct=k, total=tot_, answered=ans, t=t) for sd, side, k, tot_, ans, t in c.execute(
        "select set_name,side,correct,total,answered,max(t) from shadow_results group by set_name,side order by set_name,side")]
    out["cooling"] = [s for (s,) in c.execute("select name from seats where catalog is not null") if routing.cooling(c, s)]
    return out
