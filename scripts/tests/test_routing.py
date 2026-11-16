# -*- coding: utf-8 -*-
"""เทส routing + guardrails  รัน: python3 scripts/tests/test_routing.py   (ใช้ DB ชั่วคราว ไม่แตะ ~/.ai-team/team.db ไม่เรียกเครือข่ายจริง)"""
import os, sys, tempfile, threading, time, unittest

_tmp = tempfile.mkdtemp(prefix="aiteam-test-")
os.environ["AI_TEAM_DB"] = os.path.join(_tmp, "team.db")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import team, routing  # noqa: E402


def fake_chat_factory(usage=(100, 50), text="ok", fail=None):
    calls = []

    def fake(url, key, model, role, brief, timeout=180, max_tokens=None, extra=None):
        calls.append(model)
        fake.extras.append(extra)
        routing.tl.usage = None; routing.tl.finish = None; routing.tl.xapplied = sorted(extra) if extra else None
        if fail and model in fail:
            raise RuntimeError(fail[model])
        if usage:
            routing.tl.usage = {"prompt_tokens": usage[0], "completion_tokens": usage[1]}
        return text
    fake.calls = calls; fake.extras = []
    return fake


class Base(unittest.TestCase):
    def setUp(self):
        self.c = team.db()
        for t in ("spend", "audit", "tasks", "seats", "catalog", "gateways", "seat_skills", "catalog_extra"):
            self.c.execute(f"delete from {t}")
        self.c.execute("delete from settings where k like 'cap:%' or k like 'guard:%' or k like 'route:%' or k like 'jobcap:%'")
        self.c.commit()
        routing._warned.clear()
        self.real_chat = team.chat

    def tearDown(self):
        team.chat = self.real_chat

    def seat(self, name, cin=None, cout=None, url="https://gw.test/v1", skills=None):
        gw = url.split("//")[1].split("/")[0]
        self.c.execute("insert or ignore into gateways values(?,?,?)", (gw, url, "k"))
        self.c.execute("insert or replace into catalog(name,gateway,model,cin,cout) values(?,?,?,?,?)", (name + "-m", gw, name + "-m", cin, cout))
        self.c.execute("insert or replace into seats(name,title,role,catalog) values(?,?,?,?)", (name, name, "role", name + "-m"))
        for sk, sc in (skills or {}).items():
            self.c.execute("insert or replace into seat_skills(seat,skill,score,n) values(?,?,?,0)", (name, sk, sc))
        self.c.commit()

    def setv(self, k, v):
        self.c.execute("insert or replace into settings values(?,?)", (k, str(v))); self.c.commit()


class TestGuardrails(Base):
    def test_concurrent_reservations_never_exceed_cap(self):
        self.seat("a", 0, 0)
        self.setv("guard:reserve_out", 1000)
        self.setv("cap:day_tokens", 5000)
        res = []
        lk = threading.Lock()

        def go():
            c = team.db()
            r = routing.reserve(c, "a", "a-m", 0, 0, "", "x" * 30, "j")
            with lk:
                res.append(r[0])
        ths = [threading.Thread(target=go) for _ in range(24)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(res.count("ok"), 4, res)  # 4×(10+1000) ≤ 5000 < 5×
        self.assertEqual(res.count("block"), 20)
        tok, _, _ = routing.used(self.c, 0)
        self.assertLessEqual(tok, 5000)

    def test_settle_uses_real_usage_and_failure_releases(self):
        self.seat("a", 1.0, 2.0)
        k, _, sid = routing.reserve(self.c, "a", "a-m", 1.0, 2.0, "", "hello", "j")
        self.assertEqual(k, "ok")
        routing.settle(self.c, sid, {"prompt_tokens": 100, "completion_tokens": 50}, "r", 1.0, 2.0)
        tok, usd, _ = routing.used(self.c, 0)
        self.assertEqual(tok, 150)
        self.assertAlmostEqual(usd, (100 * 1.0 + 50 * 2.0) / 1e6)
        _, _, sid2 = routing.reserve(self.c, "a", "a-m", 1.0, 2.0, "", "hello", "j")
        routing.release(self.c, sid2)
        self.assertEqual(routing.used(self.c, 0)[0], 150)

    def test_settle_without_usage_falls_back_to_estimate(self):
        _, _, sid = routing.reserve(self.c, "a", "a-m", 0, 0, "", "x" * 300, "j")
        routing.settle(self.c, sid, None, "y" * 30, 0, 0)
        r = self.c.execute("select in_tok,out_tok,src from spend where id=?", (sid,)).fetchone()
        self.assertEqual(r, (100, 10, "est"))

    def test_usd_cap_blocks(self):
        self.setv("cap:day_usd", 0.001)
        k, msg, _ = routing.reserve(self.c, "a", "a-m", 10.0, 10.0, "", "x" * 30, "j")  # 2000 tok × $10/M = $0.02
        self.assertEqual(k, "block"); self.assertIn("USD", msg)

    def test_unpriced_is_not_free_under_usd_cap(self):
        self.setv("cap:day_usd", 1)
        self.assertEqual(routing.reserve(self.c, "a", "a-m", None, None, "", "hi", "j")[0], "skip")
        self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "j")[0], "ok")  # ตั้ง 0 เอง = ฟรีที่ผู้ใช้ยืนยัน
        self.assertEqual(routing.reserve(self.c, "a", "a-m", None, None, "", "hi", "j", cli=True)[0], "ok")
        self.setv("guard:unpriced", "allow")
        self.assertEqual(routing.reserve(self.c, "a", "a-m", None, None, "", "hi", "j")[0], "ok")

    def test_unpriced_ok_without_usd_cap_but_token_cap_still_applies(self):
        self.setv("cap:job_tokens", 100)
        k, msg, _ = routing.reserve(self.c, "a", "a-m", None, None, "", "hi", "j")
        self.assertEqual(k, "block"); self.assertIn("โทเคน", msg)

    def test_default_job_cap_is_off(self):
        self.assertEqual(routing.caps(self.c, "job", "train"), (None, None))
        for _ in range(3):  # ชื่องานซ้ำ (เช่น train) ไม่ควรสะสมจนติดเพดานเอง
            self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "x" * 3000, "train")[0], "ok")

    def test_per_job_cap_override(self):
        self.setv("cap:job_tokens", 100000)
        routing.budget_cmd(self.c, ["job", "tiny", "tokens=500"])
        self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "tiny")[0], "block")
        self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "other")[0], "ok")

    def test_disabled_guard_is_passthrough(self):
        self.setv("cap:day_tokens", 1); routing.budget_cmd(self.c, ["set", "enabled", "off"])
        self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "j"), ("ok", "", None))

    def test_stale_reservation_expires_but_still_counts_until_reaped(self):
        _, _, sid = routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "j")
        self.c.execute("update spend set t=? where id=?", (time.time() - 5000, sid)); self.c.commit()
        routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "j")
        self.assertEqual(self.c.execute("select state from spend where id=?", (sid,)).fetchone()[0], "expired")
        self.assertGreater(routing.used(self.c, 0)[0], 2000)
        routing.budget_cmd(self.c, ["reap"])
        self.assertEqual(self.c.execute("select state from spend where id=?", (sid,)).fetchone()[0], "failed")


class TestCallIntegration(Base):
    def test_call_settles_usage_and_blocks_at_cap(self):
        self.seat("a", 0, 0)
        team.chat = fake_chat_factory(usage=(1500, 1000))
        self.setv("guard:reserve_out", 1000); self.setv("cap:day_tokens", 3000)
        ok, _ = team.call(self.c, "a", "hello", job="j")
        self.assertTrue(ok)
        self.assertEqual(routing.used(self.c, 0)[0], 2500)  # ยอดจริง ไม่ใช่ค่าประมาณ
        ok, text = team.call(self.c, "a", "hello", job="j")
        self.assertFalse(ok); self.assertTrue(text.startswith("[BLOCKED]"))
        self.assertEqual(self.c.execute("select count(*) from audit where blocked like 'งบโทเคน%'").fetchone()[0], 1)

    def test_failed_call_releases_budget(self):
        self.seat("a", 0, 0)
        team.chat = fake_chat_factory(fail={"a-m": "HTTP 500"})
        ok, _ = team.call(self.c, "a", "hello", job="j")
        self.assertFalse(ok)
        self.assertEqual(routing.used(self.c, 0)[0], 0)

    def test_unpriced_seat_skipped_pool_continues_to_priced_seat(self):
        self.seat("nop"); self.seat("free", 0, 0)
        self.setv("cap:day_usd", 5)
        team.chat = fake_chat_factory()
        ok, text, used_seat, err = team.try_pool(self.c, ["nop", "free"], "hello", "j", None, {"_per_gw": 3}, {}, threading.Lock())
        self.assertTrue(ok); self.assertEqual(used_seat, "free")
        self.assertEqual(team.chat.calls, ["free-m"])

    def test_all_seats_skipped_ends_blocked_without_retry_sleep(self):
        self.seat("n1"); self.seat("n2")
        self.setv("cap:day_usd", 5)
        team.chat = fake_chat_factory()
        t0 = time.time()
        ok, text, seat, err = team.try_pool(self.c, ["n1", "n2"], "hi", "j", None, {"_per_gw": 3}, {}, threading.Lock())
        self.assertEqual((ok, err, team.chat.calls), (False, "blocked", [])); self.assertLess(time.time() - t0, 3)

    def test_run_budget_refuses_unknown_price(self):
        self.seat("nop")
        import argparse
        a = argparse.Namespace(seats="nop", assign=None, mode="best-of-n", brief="hi", budget=1.0, job=None)
        with self.assertRaises(SystemExit) as cm:
            team.run(self.c, a)
        self.assertIn("ยังไม่ตั้งราคา", str(cm.exception))


class TestRouting(Base):
    def test_classify(self):
        self.assertEqual(routing.classify("hi", None, "short_answer")[0], "light")
        self.assertEqual(routing.classify("x" * 3000, "py:t.py", "code")[0], "heavy")
        self.assertEqual(routing.classify("x" * 1000, None, None)[0], "standard")
        self.assertEqual(routing.classify("hi", None, None, "heavy"), ("heavy", "ผู้สั่งระบุ"))

    def _rank(self, tier, skill="code"):
        sc = lambda s: team.skill_score(self.c, s, skill)
        return routing.rank(self.c, ["cheap", "mid", "best"], tier, sc, lambda s: 1000)

    def test_cheapest_eligible_first_but_best_for_heavy(self):
        self.seat("cheap", 0, 0, skills={"code": 0.55})
        self.seat("mid", 1, 1, skills={"code": 0.8})
        self.seat("best", 5, 5, skills={"code": 0.95})
        self.assertEqual(self._rank("light")[0], "cheap")
        self.assertEqual(self._rank("standard")[0], "cheap")
        self.assertEqual(self._rank("heavy")[0], "best")  # cheap ต่ำกว่าเกณฑ์ heavy 0.7 → ไปท้ายแถว
        self.assertEqual(self._rank("heavy")[-1], "cheap")

    def test_cli_seat_is_not_treated_as_free_for_ranking(self):
        self.seat("api1", skills={"code": 0.5}); self.seat("api2", skills={"code": 0.5})
        self.seat("cli", url="cli://codex", skills={"code": 0.5})
        sc = lambda s: team.skill_score(self.c, s, "code")
        o = routing.rank(self.c, ["cli", "api1", "api2"], "standard", sc, lambda s: 9000 if s == "cli" else 1000)
        self.assertEqual(o[-1], "cli")  # เสมอกันเรื่องราคา → ตัดสินที่ความเร็ว (เดิม CLI ชนะเพราะนับเป็น $0)
        self.assertIsNone(routing.unit_cost(self.c, "cli"))

    def test_below_bar_everywhere_still_returns_best_available(self):
        self.seat("cheap", 0, 0, skills={"code": 0.35}); self.seat("mid", 1, 1, skills={"code": 0.4}); self.seat("best", 5, 5, skills={"code": 0.45})
        self.assertEqual(self._rank("heavy")[0], "best")

    def test_cooling_seat_skipped_until_success_or_expiry(self):
        self.seat("cheap", 0, 0, skills={"code": 0.9}); self.seat("mid", 1, 1, skills={"code": 0.9}); self.seat("best", 5, 5, skills={"code": 0.9})
        self.c.execute("insert into audit(t,agent,model,brief,blocked) values(?,?,?,?,?)", (time.time(), "cheap", "m", "b", 'error: HTTP 402 {"code":"insufficient_balance"}')); self.c.commit()
        self.assertNotIn("cheap", self._rank("light")[:2])
        self.assertTrue(routing.cooling(self.c, "cheap"))
        self.c.execute("insert into audit(t,agent,model,brief,reply) values(?,?,?,?,?)", (time.time() + 1, "cheap", "m", "b", "ok")); self.c.commit()
        self.assertFalse(routing.cooling(self.c, "cheap"))
        self.assertEqual(routing.cooldown('error: HTTP 429 {"retry_after":12}'), 12)
        self.assertEqual(routing.cooldown("error: timeout"), 0)

    def test_submit_stores_tier_and_off_switch(self):
        self.seat("a", 0, 0)
        t1 = team.submit(self.c, "hi", "a", job="j")
        t2 = team.submit(self.c, "hi", "a", job="j", tier="off")
        t3 = team.submit(self.c, "hi", "a", job="j", tier="heavy")
        g = lambda i: self.c.execute("select tier from tasks where id=?", (i,)).fetchone()[0]
        self.assertEqual((g(t1), g(t2), g(t3)), ("light", None, "heavy"))
        with self.assertRaises(ValueError):
            team.submit(self.c, "hi", "a", tier="bogus")

    def test_cascade_escalates_once_on_check_failure(self):
        self.seat("cheap", 0, 0, skills={"code": 0.55}); self.seat("best", 5, 5, skills={"code": 0.95})
        team.chat = fake_chat_factory(text="plain text")
        tid = team.submit(self.c, "x" * 3000, "cheap,best", job="j", chk="json", needs="code", tier="standard")
        t = team.claim(self.c)
        team.process(self.c, t, {"_per_gw": 3}, {}, threading.Lock())
        row = self.c.execute("select status,tier,escalated,used_seat,route_note from tasks where id=?", (tid,)).fetchone()
        self.assertEqual(row[:3], ("queued", "heavy", 1), row)
        self.assertEqual(team.chat.calls, ["cheap-m"])
        t = team.claim(self.c)  # รอบสอง: heavy → best
        team.process(self.c, t, {"_per_gw": 3}, {}, threading.Lock())
        row = self.c.execute("select status,escalated,used_seat from tasks where id=?", (tid,)).fetchone()
        self.assertEqual(row, ("check_failed", 1, "best"))  # ครบจำนวนรอบยกระดับแล้ว ไม่วนซ้ำ
        self.assertEqual(team.chat.calls, ["cheap-m", "best-m"])

    def test_no_escalation_when_it_would_pick_same_seat(self):
        self.seat("only", 0, 0, skills={"code": 0.9})
        team.chat = fake_chat_factory(text="plain")
        tid = team.submit(self.c, "x" * 3000, "only", job="j", chk="json", needs="code", tier="standard")
        team.process(self.c, team.claim(self.c), {"_per_gw": 3}, {}, threading.Lock())
        self.assertEqual(self.c.execute("select status,escalated from tasks where id=?", (tid,)).fetchone(), ("check_failed", 0))


class TestRealChatUsage(Base):
    def test_chat_captures_usage_and_max_tokens_via_local_stub(self):
        import http.server, json
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                out = json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.seat("a", 1.0, 1.0, url=f"http://127.0.0.1:{srv.server_port}/v1")
            self.setv("guard:max_tokens", 123)
            ok, text = team.call(self.c, "a", "hello", job="j")
        finally:
            srv.shutdown()
        self.assertTrue(ok); self.assertEqual(text, "hi")
        self.assertEqual(seen["body"]["max_tokens"], 123)
        self.assertEqual(self.c.execute("select in_tok,out_tok,src,state from spend").fetchone(), (7, 3, "usage", "settled"))


class TestDecide(Base):
    def setUp(self):
        super().setUp()
        self.c.execute("delete from decisions")
        self.c.execute("delete from settings where k like 'exec:%' or k like 'route:%'"); self.c.commit()
        self.seat("f1", 0, 0); self.seats = ["f1"]

    def d(self, brief, **kw):
        kw.setdefault("team_seats", self.seats)
        return routing.decide(self.c, brief, **kw)

    def test_click_heavy_goes_to_sonnet(self):
        d = self.d("เข้า Odoo กดสร้างใบสั่งซื้อ 50 ใบ กรอกข้อมูลทุกใบจากไฟล์")
        self.assertEqual((d["executor"], d["lane"], d["needs_tools"]), ("subagent:sonnet", "action", True))

    def test_judgment_goes_to_opus_even_without_tools(self):  # โหมด balanced (โหมดเดิม)
        self.setv("route:mode", "balanced")
        d = self.d("ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้ พร้อมข้อดีข้อเสีย")
        self.assertEqual((d["executor"], d["lane"], d["needs_tools"]), ("subagent:opus", "judgment", False))

    def test_stakes_force_opus_and_heavy(self):
        d = self.d("ลบข้อมูลลูกค้าที่ซ้ำออกจากระบบ")
        self.assertEqual((d["executor"], d["tier"], d["stakes"]), ("subagent:opus", "heavy", True))

    def test_light_verifiable_chat_goes_to_free_team(self):
        d = self.d("สรุปข้อความนี้เป็น 3 บรรทัด", chk="notempty")
        self.assertEqual((d["executor"], d["lane"], d["pool"]), ("team", "bulk", "@auto"))
        self.assertIn("team.py submit", d["how"])

    def test_bulk_with_tools_uses_haiku(self):
        d = self.d("จัดรูปแบบไฟล์ทุกไฟล์ใน repo ให้เป็นตัวพิมพ์เล็ก", chk="notempty", tools="yes")
        self.assertEqual(d["executor"], "subagent:haiku")

    def test_no_healthy_team_seat_falls_back_to_haiku(self):
        d = self.d("สรุปข้อความนี้", chk="notempty", team_seats=[])
        self.assertEqual(d["executor"], "subagent:haiku")

    def test_escalation_chain_then_ask_user(self):
        self.assertEqual(self.d("กรอกฟอร์มทุกหน้า", failed=2, prev="team")["executor"], "subagent:sonnet")
        self.assertEqual(self.d("กรอกฟอร์มทุกหน้า", failed=2, prev="sonnet")["executor"], "subagent:opus")
        self.assertEqual(self.d("กรอกฟอร์มทุกหน้า", failed=2, prev="opus")["executor"], "ask-user")
        routing.lanes_cmd(self.c, ["set", "top", "fable"])
        self.assertEqual(self.d("กรอกฟอร์มทุกหน้า", failed=2, prev="opus")["executor"], "subagent:fable")
        self.assertEqual(self.d("กรอกฟอร์มทุกหน้า", failed=1, prev="sonnet")["executor"], "subagent:sonnet")  # ยังไม่ถึงเกณฑ์

    def test_opus_share_cap_downgrades_non_stakes_only(self):
        self.setv("route:mode", "balanced")
        for _ in range(3):
            self.assertEqual(self.d("ช่วยวางแผนออกแบบระบบ", job="J")["executor"], "subagent:opus")
        self.d("กรอกฟอร์ม", job="J")
        d = self.d("ช่วยวางแผนออกแบบระบบ", job="J")  # opus 3/4 แล้ว → เกิน 30%
        self.assertEqual(d["executor"], "subagent:sonnet"); self.assertTrue(any("สัดส่วน Opus" in w for w in d["reason"]))
        self.assertEqual(self.d("ลบข้อมูลลูกค้าทั้งหมด", job="J")["executor"], "subagent:opus")  # เสี่ยงสูงไม่ถูกลดระดับ

    def test_nouns_are_not_actions(self):  # Odoo/commit/แก้โค้ด เป็นหัวข้อ ไม่ใช่การลงมือ
        self.assertEqual(self.d("เขียนเคสทดสอบ Odoo เป็น JSON ให้ใบสั่งขาย", chk="spec")["executor"], "team")
        self.assertEqual(self.d("แก้โค้ดฟังก์ชันตรวจเลขบัตรให้ผ่านเทส", chk="py:t.py")["executor"], "team")
        self.assertFalse(self.d("สรุปฟีเจอร์ Odoo 20 ให้หน่อย")["needs_tools"])
        self.assertFalse(self.d("ช่วยเขียน commit message จาก diff นี้")["needs_tools"])
        self.assertEqual(self.d("แก้โค้ดให้ผ่านเทส", chk="py:t.py", tools="yes")["executor"], "subagent:sonnet")  # ผู้สั่งชี้ขาด

    def test_button_label_is_not_a_stakes_action(self):  # "ปุ่มส่งข้อความ" คือชื่อปุ่มที่กดทดสอบ ไม่ใช่การส่งข้อความจริง
        d = self.d("เปิด chat-inbox แล้วกดทดสอบปุ่มส่งข้อความและหน้าตั้งค่าทุกหน้า แล้วจดว่าปุ่มไหนพัง")
        self.assertEqual((d["executor"], d["stakes"]), ("subagent:sonnet", False))
        self.assertTrue(self.d("ส่งข้อความหาลูกค้าทุกคนว่าสินค้าหมด")["stakes"])
        self.assertTrue(self.d("send an email to all customers")["stakes"])

    def test_weak_judgment_signal_with_check_goes_to_team_but_strong_does_not(self):
        self.setv("route:mode", "balanced")
        self.assertEqual(self.d("รวบรวมเทคนิคลด token พร้อมข้อดีข้อเสียสั้นๆ", chk="notempty")["executor"], "team")
        self.assertEqual(self.d("รวบรวมเทคนิคลด token พร้อมข้อดีข้อเสียสั้นๆ")["executor"], "subagent:opus")  # ไม่มีตัวเช็ก = ไม่ปล่อยทีมฟรี
        self.assertEqual(self.d("ตัดสินใจเลือกแนวทางออกแบบระบบ พร้อมข้อดีข้อเสีย", chk="notempty")["executor"], "subagent:opus")

    def test_thai_substring_false_positives(self):
        self.assertFalse(self.d("คำนวณบวกลบเลขให้หน่อย")["stakes"])
        self.assertFalse(self.d("เราไม่อยากกดดันลูกค้า ช่วยร่างข้อความสุภาพ")["needs_tools"])
        self.assertTrue(self.d("ลบข้อมูลลูกค้า")["stakes"])

    def test_stakes_reason_explains_opus_decides_sonnet_executes(self):
        d = self.d("คืนเงินลูกค้า 3 ราย และลบออเดอร์ที่ซ้ำ")
        self.assertTrue(any("ผู้ใช้ยืนยัน" in w for w in d["reason"]))

    def test_lanes_config_validates_and_results_recorded(self):
        with self.assertRaises(ValueError):
            routing.lanes_cmd(self.c, ["set", "action", "gpt"])
        routing.lanes_cmd(self.c, ["set", "action", "haiku"])
        self.assertEqual(self.d("กดปุ่มบันทึกทุกหน้า")["executor"], "subagent:haiku")
        d = self.d("กดปุ่มบันทึกทุกหน้า"); routing.set_result(self.c, d["id"], "fail")
        self.assertIn("ไม่ผ่าน 1", "\n".join(routing.lanes_cmd(self.c, [])))
        with self.assertRaises(ValueError):
            routing.set_result(self.c, 9999, "pass")


class TestFreeFirst(TestDecide):
    """ค่าเริ่มต้น: ทีมฟรีทำเต็มที่ Claude เฉพาะลงมือกด/เสี่ยงสูง/ทีมฟรีล้มซ้ำ/เลนที่ผลจริงบอกว่าฟรีไม่ไหว"""
    def test_non_stakes_judgment_goes_to_free_team_at_heavy_tier_with_head_review(self):
        d = self.d("ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้ พร้อมข้อดีข้อเสีย")
        self.assertEqual((d["executor"], d["lane"], d["tier"], d["pool"]), ("team", "judgment", "heavy", "@auto"))
        self.assertTrue(d["head_review"]); self.assertIn("--tier heavy", d["how"])
        self.assertEqual(d["skill"], "decision_calc"); self.assertIn("--needs decision_calc", d["how"])

    def test_explicit_skill_is_passed_through_to_submit(self):
        d = self.d("สรุปข้อความนี้เป็น 3 บรรทัด", chk="notempty", skill="short_answer")
        self.assertIn("--needs short_answer", d["how"])

    def test_stakes_still_opus_with_free_predraft(self):
        d = self.d("ลบข้อมูลลูกค้าที่ซ้ำออกจากระบบ")
        self.assertEqual((d["executor"], d["stakes"], d.get("pre_draft")), ("subagent:opus", True, True))
        self.assertIn("team.py submit", d["pre_draft_how"])

    def test_stakes_no_predraft_when_no_team_seats(self):
        self.assertFalse(self.d("ลบข้อมูลลูกค้าที่ซ้ำออกจากระบบ", team_seats=[]).get("pre_draft"))

    def test_click_work_still_claude_sonnet(self):
        self.assertEqual(self.d("เข้า Odoo กดสร้างใบสั่งซื้อ 50 ใบ กรอกข้อมูลทุกใบจากไฟล์")["executor"], "subagent:sonnet")

    def test_balanced_mode_restores_opus_for_judgment(self):
        self.assertIn("free-first", "\n".join(routing.lanes_cmd(self.c, [])))
        out = routing.lanes_cmd(self.c, ["set", "mode", "balanced"]); self.assertIn("โหมด", "\n".join(out) or "โหมด")
        self.assertEqual(self.d("ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้")["executor"], "subagent:opus")
        with self.assertRaises(ValueError): routing.lanes_cmd(self.c, ["set", "mode", "x"])

    def _record(self, lane_brief, n, passes):
        for i in range(n):
            did = self.d(lane_brief)["id"]; routing.set_result(self.c, did, "pass" if i < passes else "fail")

    def test_learned_lane_with_poor_free_results_skips_to_claude(self):
        brief = "ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้ พร้อมข้อดีข้อเสีย"
        self._record(brief, 5, 2)  # ผ่าน 40% < 60%
        d = self.d(brief)
        self.assertEqual(d["executor"], "subagent:opus"); self.assertTrue(any("ผ่าน 40%" in w for w in d["reason"]))
        self.assertIn("ข้ามไป Claude ตรง", "\n".join(routing.lanes_cmd(self.c, [])))

    def test_learning_needs_enough_samples_and_good_results_keep_free(self):
        brief = "ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้ พร้อมข้อดีข้อเสีย"
        self._record(brief, 4, 0)  # แย่แต่ตัวอย่างน้อยเกินไป
        self.assertEqual(self.d(brief)["executor"], "team")
        self.c.execute("delete from decisions"); self.c.commit()
        self._record(brief, 6, 5)
        self.assertEqual(self.d(brief)["executor"], "team")

    def test_shadow_results_do_not_feed_lane_learning(self):
        for _ in range(6):
            did = self.d("ช่วยตัดสินใจเลือกแนวทางออกแบบสถาปัตยกรรมระหว่างสองแบบนี้", job="shadow")["id"]; routing.set_result(self.c, did, "fail")
        self.assertIsNone(routing.lane_stats(self.c, "judgment"))

    def test_share_line_in_lanes_report(self):
        self.d("สรุปข้อความนี้เป็น 3 บรรทัด", chk="notempty"); self.d("ลบข้อมูลลูกค้า")
        out = "\n".join(routing.lanes_cmd(self.c, []))
        self.assertIn("ทีมฟรี 1/2 (50%)", out); self.assertIn("Claude 1/2 (50%)", out)


class TestTokenAccounting(Base):
    def setUp(self):
        super().setUp()
        self.c.execute("delete from decisions"); self.c.commit()

    def test_reasoning_tokens_and_raw_usage_stored(self):
        self.seat("a", 0, 0)
        team.chat = lambda *a, **k: (setattr(routing.tl, "usage", {"prompt_tokens": 10, "completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 700}}), "ok")[1]
        team.call(self.c, "a", "hi", job="j")
        r = self.c.execute("select in_tok,out_tok,reason_tok,usage_raw from spend").fetchone()
        self.assertEqual(r[:3], (10, 900, 700)); self.assertIn("reasoning_tokens", r[3])
        self.assertIn("โทเคนคิด 700 = 78%", "\n".join(routing.token_report(self.c)))
        team.chat = fake_chat_factory(usage=(5, 9100)); team.call(self.c, "a", "hi", job="j")  # ครั้งที่ผู้ให้บริการไม่แยกโทเคนคิด ห้ามถูกนับเป็นฐานของเปอร์เซ็นต์
        self.assertIn("โทเคนคิด 700 = 78%", "\n".join(routing.token_report(self.c)))
        routing.claude_log(self.c, "opus", 5000)
        self.assertNotIn("claude:opus", "\n".join(routing.spend_report(self.c)))

    def test_missing_reasoning_detail_is_reported_as_unknown_not_zero(self):
        self.seat("a", 0, 0); team.chat = fake_chat_factory(usage=(5, 50)); team.call(self.c, "a", "hi", job="j")
        self.assertIsNone(self.c.execute("select reason_tok from spend").fetchone()[0])
        self.assertIn("ยังไม่แยกโทเคนคิด", "\n".join(routing.token_report(self.c)))

    def test_claude_tokens_are_separate_from_team_caps(self):
        self.setv("cap:day_tokens", 1000)
        msg = routing.claude_log(self.c, "opus", 86692, job="J")
        self.assertIn("86,692", msg)
        self.assertEqual(routing.used(self.c, 0)[0], 0)  # เพดานทีมไม่ถูกแตะ
        self.assertEqual(routing.reserve(self.c, "a", "a-m", 0, 0, "", "hi", "J", cli=True)[0], "block")  # ทีมยังคุมด้วยเพดานเดิม (ขอ ~2,000 > 1,000)
        self.assertEqual(routing.used(self.c, 0, None, "claude")[0], 86692)

    def test_claude_log_validation_duplicate_guard_and_cap_warning(self):
        d = routing.decide(self.c, "ออกแบบสถาปัตยกรรมระบบ", job="J", team_seats=[])
        routing.claude_log(self.c, "opus", 1000, decision_id=d["id"])
        self.assertEqual(self.c.execute("select lane,job,family from spend where family='claude'").fetchone(), ("judgment", "J", "claude"))
        with self.assertRaises(ValueError): routing.claude_log(self.c, "opus", 1000, decision_id=d["id"])
        with self.assertRaises(ValueError): routing.claude_log(self.c, "gpt", 10)
        with self.assertRaises(ValueError): routing.claude_log(self.c, "opus", 0)
        with self.assertRaises(ValueError): routing.claude_log(self.c, "opus", 10, decision_id=9999)
        self.setv("cap:claude_day_tokens", 1500)
        self.assertIn("เกินเพดาน Claude", routing.claude_log(self.c, "sonnet", 900, job="J"))

    def test_process_tags_task_and_tier_and_reports_escalation_cost(self):
        self.seat("cheap", 0, 0, skills={"code": 0.55}); self.seat("best", 5, 5, skills={"code": 0.95})
        team.chat = fake_chat_factory(usage=(100, 400), text="plain")
        tid = team.submit(self.c, "x" * 3000, "cheap,best", job="J", chk="json", needs="code", tier="standard")
        for _ in range(2):
            team.process(self.c, team.claim(self.c), {"_per_gw": 3}, {}, threading.Lock())
        rows = self.c.execute("select tier, task_id from spend order by id").fetchall()
        self.assertEqual(rows, [("standard", tid), ("heavy", tid)])
        rep = "\n".join(routing.token_report(self.c))
        self.assertIn("standard: 1 งาน 500 โทเคน", rep); self.assertIn("heavy: 1 งาน 500 โทเคน", rep)
        self.assertIsNone(getattr(routing.tl, "task", None))  # ไม่ค้างค่าข้ามงาน
        self.assertEqual(getattr(routing.tl, "esc", 0), 0)


class TestReasoningExtra(Base):
    """พารามิเตอร์ลดการคิดต่อโมเดล: ใช้เฉพาะรอบแรกของงานเบา/มาตรฐาน ไม่ใช้ตอนยกระดับ ไม่พังเมื่อผู้ให้บริการปฏิเสธ"""
    def _stub(self, handler):
        import http.server, json
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); seen.append(body)
                code, resp = handler(body)
                out = json.dumps(resp).encode()
                self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv, seen

    OK = lambda self, body: (200, {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 8, "completion_tokens_details": {"reasoning_tokens": 2}}})

    def test_extra_for_gating(self):
        self.seat("a", 0, 0); routing.extra_set(self.c, "a-m", {"reasoning_effort": "low"})
        self.assertEqual(routing.extra_for(self.c, "a-m", "light", 0), {"reasoning_effort": "low"})
        self.assertEqual(routing.extra_for(self.c, "a-m", "standard", 0), {"reasoning_effort": "low"})
        self.assertEqual(routing.extra_for(self.c, "a-m", "heavy", 0), {})      # งานหนักคิดเต็ม
        self.assertEqual(routing.extra_for(self.c, "a-m", "light", 1), {})      # รอบยกระดับคิดเต็ม
        self.assertEqual(routing.extra_for(self.c, "a-m", None, 0), {})         # เรียกตรงไม่มีระดับ ไม่แตะ
        self.assertEqual(routing.extra_for(self.c, "zzz", "light", 0), {})

    def test_extra_set_validation_and_survives_catalog_replace(self):
        self.seat("a", 0, 0)
        for bad in ('{}', '[1]', '{"model":"x"}', '{"messages":[]}'):
            with self.assertRaises(ValueError): routing.extra_set(self.c, "a-m", bad)
        with self.assertRaises(ValueError): routing.extra_set(self.c, "a-m", {"x": 1}, "light,huge")
        with self.assertRaises(ValueError): routing.extra_set(self.c, "nope", {"x": 1})
        routing.extra_set(self.c, "a-m", {"reasoning_effort": "low"})
        self.c.execute("insert or replace into catalog(name,gateway,model,cin,cout) values('a-m','gw.test','a-m',0,0)"); self.c.commit()  # แบบเดียวกับ catalog add/connect
        self.assertEqual(routing.extra_for(self.c, "a-m", "light"), {"reasoning_effort": "low"})
        routing.extra_clear(self.c, "a-m"); self.assertEqual(routing.extra_for(self.c, "a-m", "light"), {})

    def test_payload_has_extra_on_first_attempt_not_on_escalation_and_records_xparam(self):
        srv, seen = self._stub(self.OK)
        self.seat("a", 0, 0, url=f"http://127.0.0.1:{srv.server_port}/v1"); routing.extra_set(self.c, "a-m", {"reasoning_effort": "low"})
        routing.tl.tier, routing.tl.esc = "light", 0
        try:
            self.assertTrue(team.call(self.c, "a", "hi", job="j")[0])
            routing.tl.esc = 1  # รอบยกระดับ
            self.assertTrue(team.call(self.c, "a", "hi", job="j")[0])
            routing.tl.tier, routing.tl.esc = "heavy", 0
            self.assertTrue(team.call(self.c, "a", "hi", job="j")[0])
        finally:
            routing.tl.tier = None; routing.tl.esc = 0
        self.assertEqual([b.get("reasoning_effort") for b in seen], ["low", None, None])
        self.assertEqual([r[0] for r in self.c.execute("select xparam from spend order by id")], ["reasoning_effort", None, None])

    def test_provider_rejects_extra_with_400_retries_without_it(self):
        srv, seen = self._stub(lambda b: (400, {"error": "unsupported"}) if "reasoning_effort" in b else self.OK(b))
        self.seat("a", 0, 0, url=f"http://127.0.0.1:{srv.server_port}/v1"); routing.extra_set(self.c, "a-m", {"reasoning_effort": "low"})
        routing.tl.tier, routing.tl.esc = "standard", 0
        try:
            ok, text = team.call(self.c, "a", "hi", job="j")
        finally:
            routing.tl.tier = None
        self.assertEqual((ok, text), (True, "hi")); self.assertEqual(len(seen), 2); self.assertNotIn("reasoning_effort", seen[1])
        self.assertIsNone(self.c.execute("select xparam from spend").fetchone()[0])  # ไม่ได้ใช้จริง ห้ามบันทึกว่าใช้

    def test_400_without_extra_is_still_an_error(self):
        srv, seen = self._stub(lambda b: (400, {"error": "bad"}))
        self.seat("a", 0, 0, url=f"http://127.0.0.1:{srv.server_port}/v1")
        ok, text = team.call(self.c, "a", "hi", job="j")
        self.assertFalse(ok); self.assertIn("HTTP 400", text); self.assertEqual(len(seen), 1)

    def test_empty_answer_with_finish_length_is_truncated_not_ok_and_not_cooled(self):
        srv, seen = self._stub(lambda b: (200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {"prompt_tokens": 5, "completion_tokens": 100, "completion_tokens_details": {"reasoning_tokens": 100}}}))
        self.seat("a", 0, 0, url=f"http://127.0.0.1:{srv.server_port}/v1"); self.setv("guard:max_tokens", 100)
        ok, text = team.call(self.c, "a", "hi", job="j")
        self.assertFalse(ok); self.assertTrue(text.startswith("[TRUNCATED]"))
        self.assertEqual(self.c.execute("select out_tok,reason_tok,state from spend").fetchone(), (100, 100, "settled"))  # โทเคนที่เสียไปยังนับ
        self.assertIn("truncated", self.c.execute("select blocked from audit").fetchone()[0])
        self.assertFalse(routing.cooling(self.c, "a"))  # ไม่ใช่ความผิดของช่อง ไม่พัก

    def test_empty_answer_with_finish_stop_is_still_returned_as_before(self):
        srv, seen = self._stub(lambda b: (200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}))
        self.seat("a", 0, 0, url=f"http://127.0.0.1:{srv.server_port}/v1")
        self.assertEqual(team.call(self.c, "a", "hi", job="j"), (True, ""))


class TestRanking(Base):
    """แท็บ Ranking/Routing: คะแนนมาจากหลักฐานจริง ปรับ Laplace ไม่นับชุด shadow ที่เทียบกันไม่ได้ ไม่หลุด key"""
    def setUp(self):
        super().setUp()
        for tb in ("shadow_results", "seat_skills", "decisions"):
            self.c.execute(f"delete from {tb}")
        self.c.commit()

    def audit(self, seat, ok=0, err=0, blocked=0):
        for _ in range(ok):
            self.c.execute("insert into audit(t,agent,model,brief,reply,ms) values(?,?,?,?,?,?)", (time.time(), seat, "m", "b", "r", 1000))
        for _ in range(err):
            self.c.execute("insert into audit(t,agent,model,brief,blocked) values(?,?,?,?,?)", (time.time(), seat, "m", "b", "error: HTTP 500"))
        for _ in range(blocked):
            self.c.execute("insert into audit(t,agent,model,brief,blocked) values(?,?,?,?,?)", (time.time(), seat, "m", "b", "เกินงบจำนวนครั้งต่อชั่วโมง"))
        self.c.commit()

    def shadow(self, set_name, side, k, n=16):
        self.c.execute("insert into shadow_results(t,set_name,side,correct,total,answered) values(?,?,?,?,?,?)", (time.time(), set_name, side, k, n, n)); self.c.commit()

    def test_better_quality_and_fewer_errors_rank_first_and_blocks_are_not_errors(self):
        import rank
        for s in ("a", "b", "c"):
            self.seat(s, 0, 0)
        self.audit("a", ok=30); self.audit("b", ok=20, err=10, blocked=40); self.audit("c", ok=30)
        for s, k in (("a", 16), ("b", 16), ("c", 8)):
            self.shadow("hard", s, k)
        r = {x["seat"]: x for x in rank.ranking(self.c)["seats"]}
        self.assertLess(r["a"]["rank"], r["b"]["rank"]); self.assertLess(r["b"]["rank"], r["c"]["rank"])  # c: ฝีมือต่ำสุด · b: ผิดพลาดมากแต่ฝีมือสูง
        self.assertEqual((r["b"]["errors"], r["b"]["blocked"]), (10, 40))  # ถูกบล็อกด้วยเพดานไม่นับเป็นความผิดของช่อง
        self.assertAlmostEqual(r["a"]["reliability"], 31 / 32.0)

    def test_laplace_small_sample_never_perfect_and_thin_flag(self):
        import rank
        self.seat("a", 0, 0); self.audit("a", ok=3); self.shadow("hard", "a", 3, 3)
        for s in ("b", "c"):
            self.seat(s, 0, 0); self.shadow("hard", s, 3, 3)
        x = [x for x in rank.ranking(self.c)["seats"] if x["seat"] == "a"][0]
        self.assertLess(x["score"], 1.0); self.assertTrue(x["thin"])

    def test_shadow_set_measured_on_fewer_than_three_seats_is_ignored(self):
        import rank
        for s in ("a", "b"):
            self.seat(s, 0, 0)
        self.shadow("easy", "a", 16); self.shadow("easy", "b", 0)
        x = [x for x in rank.ranking(self.c)["seats"] if x["seat"] == "a"][0]
        self.assertEqual(x["evidence"], []); self.assertIsNone(x["score"]); self.assertIsNone(x["rank"])  # ยังไม่วัด ไม่ได้ลำดับ

    def test_skill_scores_only_used_when_no_other_evidence_and_no_secrets_in_output(self):
        import rank, json
        self.seat("a", 0, 0, skills={"code": 0.8}); self.audit("a", ok=12)
        self.c.execute("update gateways set api_key='sk-SECRET-123'"); self.c.commit()
        out = rank.ranking(self.c); text = json.dumps(out) + json.dumps(rank.routing_view(self.c), default=str)
        self.assertNotIn("sk-SECRET-123", text)
        x = out["seats"][0]; self.assertEqual([e["name"][:8] for e in x["evidence"]], ["คะแนนควา"])
        self.shadow("hard", "a", 16); self.shadow("hard", "z1", 1); self.shadow("hard", "z2", 1)
        self.assertEqual(len(rank.ranking(self.c)["seats"][0]["evidence"]), 1)  # ชุด hard วัดช่องในทีมแค่ 1 ช่อง → ไม่นับ และคะแนนความถนัดยังเป็นหลักฐานเดียว

    def test_routing_view_excludes_shadow_decisions_and_reports_share(self):
        import rank
        self.seat("a", 0, 0)
        routing.decide(self.c, "สรุปข้อความนี้เป็น 3 บรรทัด", chk="notempty", job="shadow"); routing.decide(self.c, "ลบข้อมูลลูกค้า", job="J")
        v = rank.routing_view(self.c)
        self.assertEqual(v["share"]["total"], 1); self.assertEqual(len(v["recent"]), 1); self.assertEqual(v["mode"], "free-first")


class TestMigration(unittest.TestCase):
    def test_ensure_is_idempotent_and_additive(self):
        c = team.db(); routing.ensure(c); routing.ensure(c)
        cols = [r[1] for r in c.execute("pragma table_info(tasks)")]
        for col in ("tier", "route_note", "escalated", "status", "brief"):
            self.assertIn(col, cols)


if __name__ == "__main__":
    unittest.main(verbosity=2)
