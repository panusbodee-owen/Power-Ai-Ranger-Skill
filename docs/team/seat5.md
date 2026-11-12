# seat5 — ผู้ตรวจหลัก

- โมเดล: `unsloth/qwen3.8-27b-nvfp4`
- Gateway: `api.tokenme.limited` — base URL `https://api.tokenme.limited/api/v1`
- ตัวแปร key (ตั้งเองในเครื่อง ห้ามใส่ในไฟล์): `AI_TEAM_KEY_API_TOKENME_LIMITED`
- ผลเทรน (ชุดทดสอบ B): ก่อนใส่คู่มือ 19/20 → หลัง 20/20 (ข้อผิดพลาดระบบ 0 ครั้ง ไม่นับคะแนน) — ดูรายงานฉบับเต็มใน docs/performance-*.md

## ใช้กับงานแบบไหน

- ตอบสั้น/คำนวณ
- ทำตามรูปแบบที่สั่ง
- ผลลัพธ์ JSON ยาว
- ดึงข้อมูลเป็น JSON
- ตรวจงาน (ตัดสิน pass/fail)
- รวม/สรุปงาน
- เขียนโค้ดสั้น

## อย่าใช้กับงานแบบไหน

- (ไม่พบทักษะที่ต่ำกว่า 50%)

คะแนนทักษะจากงานจริงสะสม (ใช้จัดงานอัตโนมัติ): short_answer 0.90 (n=0), product_strategy 0.60 (n=0), review 0.56 (n=3), synthesis 0.51 (n=1)

## System prompt (ใช้ตามนี้ทุกคำ)

บทบาทประจำตำแหน่ง:

```
คุณคือผู้ตรวจงานที่เข้มงวดและเป็นกลาง: ตรวจงานเทียบกับโจทย์ทีละข้อ ห้ามให้ผ่านถ้ายังไม่แน่ใจ ทุกข้อบกพร่องต้องมีหลักฐานจากตัวงาน อย่าชมเกินจริง ตอบเป็น JSON เท่านั้น
```

คู่มือสั่งงานที่ได้จากการเทรน (เปิดใช้ในระบบแล้ว) — ต่อท้าย system prompt:

```
คำแนะนำการทำงาน (เรียนรู้จากข้อผิดพลาดที่ผ่านมาของคุณ):
- ตอบตรงตามรูปแบบที่สั่งเท่านั้น: ไม่มีคำนำ ไม่มีคำลงท้าย ไม่อธิบายเพิ่มเมื่อไม่ได้ขอ
```

ที่มา: จากข้อผิดพลาด: ไม่มี

## ตัวอย่างการสั่งงาน

**ตอบสั้น/คำนวณ**

คำสั่ง:

```
คำนวณ 19*29 ตอบเฉพาะตัวเลขอย่างเดียว
```

รูปแบบผลลัพธ์ที่คาดหวัง:

```
551
```

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

## ข้อผิดพลาดที่ยังพบ (หลังใส่คู่มือ)

- ไม่พบในชุดทดสอบ

ข้อควรรู้ทั่วไป: ตัดข้อความระหว่าง `<think>...</think>` ออกก่อนใช้ผลลัพธ์ และตรวจรูปแบบ (JSON) ด้วยโปรแกรมทุกครั้ง อย่าเชื่อว่าโมเดลทำตามรูปแบบเสมอ

## เรียกใช้ทันที

```bash
export AI_TEAM_KEY_API_TOKENME_LIMITED=...   # ใส่ key ของคุณเอง
curl -s https://api.tokenme.limited/api/v1/chat/completions \
  -H "Authorization: Bearer $AI_TEAM_KEY_API_TOKENME_LIMITED" -H 'Content-Type: application/json' \
  -d '{"model": "unsloth/qwen3.8-27b-nvfp4", "messages": [{"role":"system","content":"<system prompt จากด้านบน>"},{"role":"user","content":"<คำสั่ง>"}]}'
```

```python
import os, json, urllib.request
req = urllib.request.Request('https://api.tokenme.limited/api/v1/chat/completions',
    json.dumps({'model': 'unsloth/qwen3.8-27b-nvfp4', 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': BRIEF}]}).encode(),
    {'Authorization': 'Bearer ' + os.environ['AI_TEAM_KEY_API_TOKENME_LIMITED'], 'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content'])
```
