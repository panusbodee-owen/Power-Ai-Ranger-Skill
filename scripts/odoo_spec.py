"""DSL เคสทดสอบ Odoo ที่ AI เขียนได้อย่างปลอดภัย — ตัวตรวจ (validator)
พนักงาน AI ไม่เคยเข้าถึง Odoo: ส่งแค่ JSON ผ่านตัวตรวจนี้ แล้วให้ Claude รันผ่านเซสชันของผู้ใช้ด้วย odoo_runner.js
(ตัวรันบังคับซ้ำอีกชั้น: เขียน/สั่งงานได้เฉพาะเรคอร์ดที่สร้างระหว่างรันนี้)
"""
import json
import re

ALLOWED_MODELS = {
    "res.partner", "product.template", "product.product",
    "sale.order", "sale.order.line", "purchase.order", "purchase.order.line",
    "stock.picking", "stock.move", "stock.quant", "stock.return.picking", "stock.return.picking.line", "stock.backorder.confirmation",
    "account.move", "account.move.line", "account.payment", "account.payment.register", "account.move.reversal",
    "sale.advance.payment.inv", "sale.order.discount", "purchase.order.discount",
    "dg.billing.note", "mrp.production", "mrp.bom", "mrp.bom.line",
}
READ_ONLY_EXTRA = {"account.tax", "account.journal", "stock.picking.type", "dg.account.move.type", "dg.sale.order.type", "dg.purchase.order.type", "res.company", "account.payment.term", "stock.location"}
ALLOWED_METHODS = {
    "action_confirm", "action_post", "action_cancel", "action_draft", "button_confirm", "button_validate", "button_cancel",
    "action_create_invoice", "action_register_payment", "action_apply_discount", "create_invoices", "action_create_payments",
    "refund_moves", "action_reverse", "process", "action_create_returns", "dg_action_create_internal_transfer", "action_assign",
    "button_mark_done", "button_draft", "action_view_invoice",
}
FIXTURES = {"F.cust", "F.cust2", "F.vend", "F.sand", "F.ship", "F.cement", "F.rebar", "F.pole"}
CONSTS = {
    "C.today", "C.analytic_vat5", "C.analytic_nonvat6", "C.tax_sale_vat7", "C.tax_purch_vat7", "C.tax_sale_wht3", "C.tax_purch_wht1",
    "C.so_type_invoice", "C.so_type_receipt", "C.so_type_billslip", "C.pt_sale_front", "C.pt_recv_front", "C.pt_internal_b1_b2",
    "C.po_type_vat", "C.uom_ton", "C.uom_bag", "C.uom_unit", "C.uom_trip",
    "C.mt_inv_ar", "C.mt_inv_inv", "C.mt_billslip", "C.mt_vendor_vat", "C.mt_credit_vat", "C.tax_sale_vat0", "C.pt_mo",
}
BAD_FIELDS = {"password", "login", "groups_id", "group_ids", "api_key", "api_key_ids", "active_ids_bypass"}
BAD_DO = set()
DOS = {"create", "write", "call", "wizard", "read", "search", "assert"}
ASSERT_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "approx", "contains", "truthy", "falsy", "len_eq"}
KEYS = {"do", "model", "method", "vals", "ids", "args", "kwargs", "ctx", "as", "fields", "domain", "limit", "path", "op", "value", "tol", "expect_error", "note"}
REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)$")


def _first_json_list(text):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "[":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, list):
                return obj
    return None


def _walk_refs(x, defined, errs, where):
    if isinstance(x, str):
        if x.startswith("$"):
            m = re.match(r"^\$(F\.[A-Za-z0-9_]+|C\.[A-Za-z0-9_]+|[A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)$", x)
            if not m:
                errs.append(f"{where}: อ้างอิง '{x}' รูปแบบผิด (ใช้ $ชื่อ หรือ $ชื่อ.0.field)")
                return
            base = m.group(1)
            if base.startswith("F.") and base not in FIXTURES:
                errs.append(f"{where}: ไม่มี fixture '{base}' (มี: {', '.join(sorted(FIXTURES))})")
            elif base.startswith("C.") and base not in CONSTS:
                errs.append(f"{where}: ไม่มีค่าคงที่ '{base}' (มี: {', '.join(sorted(CONSTS))})")
            elif not base.startswith(("F.", "C.")) and base not in defined:
                errs.append(f"{where}: อ้างถึง ${base} ก่อนที่จะสร้าง/อ่านด้วย \"as\"")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _walk_refs(v, defined, errs, f"{where}[{i}]")
    elif isinstance(x, dict):
        for k, v in x.items():
            if str(k).lower() in BAD_FIELDS:
                errs.append(f"{where}: ห้ามตั้งฟิลด์ '{k}'")
            _walk_refs(v, defined, errs, f"{where}.{k}")


def validate(text):
    """คืน (ok, ข้อความอธิบายข้อผิดพลาดแบบแก้ไขได้ทันที)"""
    sc = _first_json_list(text or "")
    if sc is None:
        return False, "ต้องตอบเป็น JSON array ของ scenario (ไม่มี JSON array ในคำตอบ)"
    if not 1 <= len(sc) <= 12:
        return False, f"จำนวน scenario ต้อง 1-12 (ได้ {len(sc)})"
    errs, ids = [], set()
    for si, s in enumerate(sc):
        w = f"scenario[{si}]"
        if not isinstance(s, dict) or not s.get("id") or not isinstance(s.get("steps"), list):
            errs.append(f"{w}: ต้องเป็น object ที่มี id, title, steps[]"); continue
        if s["id"] in ids:
            errs.append(f"{w}: id '{s['id']}' ซ้ำ")
        ids.add(s["id"])
        steps = s["steps"]
        if not 1 <= len(steps) <= 40:
            errs.append(f"{w}: steps ต้อง 1-40 ขั้น (ได้ {len(steps)})")
        defined, has_check = set(), False
        for ti, st in enumerate(steps):
            ww = f"{s['id']}.step{ti + 1}"
            if not isinstance(st, dict):
                errs.append(f"{ww}: ต้องเป็น object"); continue
            bad = set(st) - KEYS
            if bad:
                errs.append(f"{ww}: คีย์ที่ไม่รู้จัก {sorted(bad)} (ใช้ได้: {sorted(KEYS)})")
            do = st.get("do")
            if do not in DOS:
                errs.append(f"{ww}: do ต้องเป็นหนึ่งใน {sorted(DOS)} (ได้ {do!r})"); continue
            m = st.get("model")
            if do != "assert":
                if not m:
                    errs.append(f"{ww}: ต้องระบุ model"); continue
                ok_models = ALLOWED_MODELS | (READ_ONLY_EXTRA if do in ("read", "search") else set())
                if m not in ok_models:
                    errs.append(f"{ww}: model '{m}' ไม่อนุญาต" + (" (ใช้อ่านได้เฉพาะ read/search)" if m in READ_ONLY_EXTRA else ""))
            if do in ("write", "call", "read") and not st.get("ids"):
                errs.append(f"{ww}: do={do} ต้องมี ids เป็นอ้างอิง เช่น [\"$so\"]")
            if do in ("write", "call", "wizard", "read"):
                for i in st.get("ids") or []:
                    if not (isinstance(i, str) and i.startswith("$")):
                        errs.append(f"{ww}: ids ต้องเป็นอ้างอิง $ชื่อ เท่านั้น (ห้ามใส่เลข id ตรงๆ) ได้ {i!r}")
            if do == "call" or do == "wizard":
                meth = st.get("method")
                if not meth:
                    errs.append(f"{ww}: ต้องระบุ method")
                elif meth.startswith("_") or meth not in ALLOWED_METHODS:
                    errs.append(f"{ww}: method '{meth}' ไม่อนุญาต (อนุญาต: {', '.join(sorted(ALLOWED_METHODS))})")
            if do == "search":
                if not isinstance(st.get("domain", []), list) or int(st.get("limit", 20)) > 50:
                    errs.append(f"{ww}: search ต้องมี domain เป็น list และ limit ≤ 50")
            if do == "assert":
                if st.get("op") not in ASSERT_OPS:
                    errs.append(f"{ww}: assert ต้องมี op ∈ {sorted(ASSERT_OPS)}")
                if not st.get("path"):
                    errs.append(f"{ww}: assert ต้องมี path เช่น \"$inv.0.amount_total\"")
                has_check = True
            if st.get("expect_error") not in (None, True, False) and not isinstance(st.get("expect_error"), str):
                errs.append(f"{ww}: expect_error ต้องเป็น true หรือข้อความที่ต้องพบใน error")
            if st.get("expect_error"):
                has_check = True
            for k in ("vals", "ids", "args", "kwargs", "ctx", "value", "domain", "path"):
                if k in st:
                    pv = st[k]
                    if k == "path":
                        _walk_refs([pv] if isinstance(pv, str) else pv, defined, errs, f"{ww}.path")
                    else:
                        _walk_refs(pv, defined, errs, f"{ww}.{k}")
            if st.get("as"):
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(st["as"])):
                    errs.append(f"{ww}: ชื่อ as ผิดรูปแบบ")
                defined.add(st["as"])
        if not has_check:
            errs.append(f"{s['id']}: ต้องมีอย่างน้อย 1 assert หรือ expect_error (ไม่งั้นไม่ได้ทดสอบอะไร)")
    if errs:
        return False, "พบ " + str(len(errs)) + " ข้อผิดพลาด แก้ให้ครบแล้วส่ง JSON ฉบับเต็มใหม่:\n- " + "\n- ".join(errs[:12])
    return True, ""
