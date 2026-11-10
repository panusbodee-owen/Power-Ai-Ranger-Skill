/* ตัวรันเคสทดสอบ Odoo (วางในคอนโซลของหน้า Odoo ที่ผู้ใช้ล็อกอินเอง ต้องมี window.rpc)
   ใช้: const out = await runScenarios(scenarios)   — scenarios ต้องผ่าน odoo_spec.validate มาก่อน
   กฎความปลอดภัย: เขียน/สั่งงานได้เฉพาะเรคอร์ดที่ "สร้างหลังเริ่มรันนี้" เท่านั้น, ไม่มี unlink, ชื่อพาร์ทเนอร์/สินค้าถูกใส่ [TEST] นำหน้า */
window.runScenarios = async function (scenarios) {
  const start = Date.now() / 1000 - 8;
  const today = new Date().toISOString().slice(0, 10);
  const one = async (model, dom) => (await rpc(model, 'search_read', [dom], { fields: ['id'], limit: 1 }))[0]?.id;
  const F = {
    cust: await one('res.partner', [['name', '=like', '[TEST] บริษัท ทดสอบวัสดุก่อสร้าง%']]),
    cust2: await one('res.partner', [['name', '=', '[TEST] ลูกค้าเบอร์ซ้ำ']]),
    vend: await one('res.partner', [['name', '=like', '[TEST] หจก.%']]),
    sand: await one('product.product', [['name', '=', '[TEST] ทรายหยาบ']]),
    ship: await one('product.product', [['name', '=', '[TEST] ค่าขนส่ง']]),
    cement: await one('product.product', [['name', '=', '[TEST] ปูนซีเมนต์']]),
    rebar: await one('product.product', [['name', '=', '[TEST] เหล็กเส้น']]),
    pole: await one('product.product', [['name', '=', '[TEST] เสาคอนกรีตอัดแรง']]),
  };
  const C = {
    today, analytic_vat5: { '5': 100 }, analytic_nonvat6: { '6': 100 },
    tax_sale_vat7: [[6, 0, [6]]], tax_purch_vat7: [[6, 0, [5]]], tax_sale_wht3: [[6, 0, [6, 21]]], tax_purch_wht1: [[6, 0, [5, 15]]],
    so_type_invoice: 6, so_type_receipt: 5, so_type_billslip: 4, pt_sale_front: 55, pt_recv_front: 54, pt_internal_b1_b2: 56, po_type_vat: 5,
    uom_ton: 104, uom_bag: 109, uom_unit: 103, uom_trip: 115,
  };
  const TRANSIENT = new Set(['account.payment.register', 'account.move.reversal', 'sale.advance.payment.inv', 'sale.order.discount',
    'purchase.order.discount', 'stock.return.picking', 'stock.return.picking.line', 'stock.backorder.confirmation']);
  const NAMED = new Set(['res.partner', 'product.template', 'product.product']);

  const getPath = (obj, parts) => parts.reduce((o, k) => (o == null ? undefined : o[k]), obj);
  const resolve = (x, env) => {
    if (typeof x === 'string' && x.startsWith('$')) {
      const parts = x.slice(1).split('.');
      let base;
      if (parts[0] === 'F' || parts[0] === 'C') { base = (parts[0] === 'F' ? F : C)[parts[1]]; parts.splice(0, 2); }
      else { base = env[parts[0]]; parts.splice(0, 1); }
      if (base === undefined) throw new Error('ไม่พบค่าอ้างอิง ' + x);
      const v = getPath(base, parts);
      if (v === undefined) throw new Error('ไม่พบ path ' + x);
      return v;
    }
    if (Array.isArray(x)) return x.map(v => resolve(v, env));
    if (x && typeof x === 'object') return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, resolve(v, env)]));
    return x;
  };
  const toIds = (v) => (Array.isArray(v) ? v.flat() : [v]).map(n => (Array.isArray(n) ? n[0] : n));
  const guard = async (model, ids) => {
    if (TRANSIENT.has(model)) return;
    const rows = await rpc(model, 'read', [ids, ['create_date']]);
    for (const r of rows) {
      if (Date.parse(r.create_date.replace(' ', 'T') + 'Z') / 1000 < start) throw new Error(`ปฏิเสธ: ${model}#${r.id} ไม่ได้สร้างในรอบนี้ (ห้ามแก้เรคอร์ดเดิม)`);
    }
  };
  const cmp = (op, a, b, tol) => ({
    eq: () => JSON.stringify(a) === JSON.stringify(b) || a === b, ne: () => a !== b, gt: () => a > b, ge: () => a >= b, lt: () => a < b, le: () => a <= b,
    approx: () => Math.abs(Number(a) - Number(b)) <= (tol ?? 0.01), contains: () => String(a).includes(String(b)),
    truthy: () => !!a, falsy: () => !a, len_eq: () => (a || []).length === b,
  }[op])();

  const results = [];
  for (const sc of scenarios) {
    const env = {}, steps = []; let failed = null;
    for (let i = 0; i < sc.steps.length && !failed; i++) {
      const st = sc.steps[i]; let ok = true, msg = '';
      try {
        const run = async () => {
          const ctx = st.ctx ? resolve(st.ctx, env) : {};
          if (st.do === 'create') {
            const vals = resolve(st.vals || {}, env);
            if (NAMED.has(st.model) && vals.name && !String(vals.name).startsWith('[TEST]')) vals.name = '[TEST] ' + vals.name;
            env[st.as || '_'] = await rpc(st.model, 'create', [vals], { context: ctx });
          } else if (st.do === 'write') {
            const ids = toIds(resolve(st.ids, env)); await guard(st.model, ids);
            await rpc(st.model, 'write', [ids, resolve(st.vals || {}, env)], { context: ctx });
          } else if (st.do === 'call') {
            const ids = toIds(resolve(st.ids, env)); await guard(st.model, ids);
            const r = await rpc(st.model, st.method, [ids, ...resolve(st.args || [], env)], { context: ctx, ...resolve(st.kwargs || {}, env) });
            if (st.as) env[st.as] = r;
          } else if (st.do === 'wizard') {
            if (ctx.active_ids) { const am = ctx.active_model; ctx.active_ids = toIds(ctx.active_ids); if (am) await guard(am, ctx.active_ids); if (!ctx.active_id) ctx.active_id = ctx.active_ids[0]; }
            const w = await rpc(st.model, 'create', [resolve(st.vals || {}, env)], { context: ctx });
            const r = await rpc(st.model, st.method, [[w]], { context: ctx });
            if (st.as) env[st.as] = r;
          } else if (st.do === 'read') {
            const ids = toIds(resolve(st.ids, env));
            env[st.as || '_'] = await rpc(st.model, 'read', [ids, st.fields || ['display_name']]);
          } else if (st.do === 'search') {
            env[st.as || '_'] = await rpc(st.model, 'search_read', [resolve(st.domain || [], env)], { fields: st.fields || ['display_name'], limit: Math.min(st.limit || 20, 50) });
          } else if (st.do === 'assert') {
            const a = resolve(st.path, env), b = st.value === undefined ? undefined : resolve(st.value, env);
            if (!cmp(st.op, a, b, st.tol)) throw new Error(`assert ล้ม: ได้ ${JSON.stringify(a)} ${st.op} ${JSON.stringify(b)}`);
          }
        };
        if (st.expect_error) {
          let threw = null; try { await run(); } catch (e) { threw = e.message; }
          if (threw === null) throw new Error('คาดว่าต้อง error แต่สำเร็จ (ระบบรับข้อมูลที่ไม่ควรรับ)');
          if (typeof st.expect_error === 'string' && !threw.toLowerCase().includes(st.expect_error.toLowerCase())) throw new Error(`error ไม่ตรงที่คาด: ได้ "${threw.slice(0, 160)}"`);
          msg = 'ถูกปฏิเสธตามคาด: ' + threw.slice(0, 100);
        } else await run();
      } catch (e) { ok = false; msg = e.message.slice(0, 300); failed = failed || { step: i + 1, msg }; }
      steps.push({ i: i + 1, do: st.do, ok, msg });
    }
    results.push({ id: sc.id, title: sc.title, pass: !failed, failed, nsteps: sc.steps.length, steps: steps.filter(s => !s.ok || s.msg) });
  }
  localStorage.setItem('tasaiRunResults', JSON.stringify(results));
  return results;
};
'RUNNER LOADED'
