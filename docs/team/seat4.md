# seat4 — สำรอง (Groq)

- โมเดล: `gpt-6-luna`
- Gateway: `codex-cli` — base URL `cli://codex`
- ตัวแปร key (ตั้งเองในเครื่อง ห้ามใส่ในไฟล์): `AI_TEAM_KEY_CODEX_CLI`
- ผลเทรน (ชุดทดสอบ B): ก่อนใส่คู่มือ 19/20 → หลัง 20/20 (ข้อผิดพลาดระบบ 0 ครั้ง ไม่นับคะแนน) — ดูรายงานฉบับเต็มใน docs/performance-*.md

## ใช้กับงานแบบไหน

- ทำตามรูปแบบที่สั่ง
- ผลลัพธ์ JSON ยาว
- ดึงข้อมูลเป็น JSON
- ตรวจงาน (ตัดสิน pass/fail)
- รวม/สรุปงาน
- เขียนโค้ดสั้น
- ตอบสั้น/คำนวณ

## อย่าใช้กับงานแบบไหน

- (ไม่พบทักษะที่ต่ำกว่า 50%)

คะแนนทักษะจากงานจริงสะสม (ใช้จัดงานอัตโนมัติ): synthesis 0.50 (n=0), analysis 0.36 (n=2)

## System prompt (ใช้ตามนี้ทุกคำ)

บทบาทประจำตำแหน่ง:

```
คุณคือผู้ทำงาน: ทำตามโจทย์ให้ครบทุกข้อ ถูกต้อง กระชับ ถ้าได้รับข้อบกพร่องจากผู้ตรวจให้แก้ทุกข้อและตอบเป็นงานฉบับแก้ไขเต็ม
```

คู่มือสั่งงานที่ได้จากการเทรน (เปิดใช้ในระบบแล้ว) — ต่อท้าย system prompt:

```
คำแนะนำการทำงาน (เรียนรู้จากข้อผิดพลาดที่ผ่านมาของคุณ):
- ตอบตรงตามรูปแบบที่สั่งเท่านั้น: ไม่มีคำนำ ไม่มีคำลงท้าย ไม่อธิบายเพิ่มเมื่อไม่ได้ขอ
- งานโค้ด: เขียน test ครบทุก route และต้องรองรับ Python 3.9 (ใส่ from __future__ import annotations)
```

ที่มา: lessons 2026-11-13 real work

## ตัวอย่างการสั่งงาน

**ทำตามรูปแบบที่สั่ง**

คำสั่ง:

```
เขียนข้อดี 3 ข้อ เป็นรายการ 3 บรรทัดพอดี ขึ้นต้นด้วย '- ' ห้ามมีข้อความอื่น
```

รูปแบบผลลัพธ์ที่คาดหวัง:

```
- ข้อ 1
- ข้อ 2
- ข้อ 3
```

**ผลลัพธ์ JSON ยาว**

คำสั่ง:

```
สร้าง JSON array 12 รายการ คีย์ id, name ตอบ JSON ล้วน
```

รูปแบบผลลัพธ์ที่คาดหวัง:

```
[{"id":1,"name":"..."}, ...]
```

## ข้อผิดพลาดที่ยังพบ (หลังใส่คู่มือ)

- ไม่พบในชุดทดสอบ

ข้อควรรู้ทั่วไป: ตัดข้อความระหว่าง `<think>...</think>` ออกก่อนใช้ผลลัพธ์ และตรวจรูปแบบ (JSON) ด้วยโปรแกรมทุกครั้ง อย่าเชื่อว่าโมเดลทำตามรูปแบบเสมอ

## เรียกใช้ทันที

```bash
export AI_TEAM_KEY_CODEX_CLI=...   # ใส่ key ของคุณเอง
curl -s cli://codex/chat/completions \
  -H "Authorization: Bearer $AI_TEAM_KEY_CODEX_CLI" -H 'Content-Type: application/json' \
  -d '{"model": "gpt-6-luna", "messages": [{"role":"system","content":"<system prompt จากด้านบน>"},{"role":"user","content":"<คำสั่ง>"}]}'
```

```python
import os, json, urllib.request
req = urllib.request.Request('cli://codex/chat/completions',
    json.dumps({'model': 'gpt-6-luna', 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': BRIEF}]}).encode(),
    {'Authorization': 'Bearer ' + os.environ['AI_TEAM_KEY_CODEX_CLI'], 'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content'])
```
