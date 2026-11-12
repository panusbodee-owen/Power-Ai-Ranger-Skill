# intern — ผู้ทดสอบ

- โมเดล: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`
- Gateway: `integrate.api.nvidia.com` — base URL `https://integrate.api.nvidia.com/v1`
- ตัวแปร key (ตั้งเองในเครื่อง ห้ามใส่ในไฟล์): `AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM`
- ผลเทรน (ชุดทดสอบ B): ก่อนใส่คู่มือ 20/20 → หลัง 20/20 (ข้อผิดพลาดระบบ 0 ครั้ง ไม่นับคะแนน) — ดูรายงานฉบับเต็มใน docs/performance-*.md

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

คะแนนทักษะจากงานจริงสะสม (ใช้จัดงานอัตโนมัติ): review 0.71 (n=1), long_output 0.70 (n=0), test_design 0.60 (n=0), short_answer 0.50 (n=0), synthesis 0.40 (n=0), analysis 0.31 (n=3), code 0.30 (n=0), odoo_spec 0.20 (n=0), product_strategy 0.20 (n=0)

## System prompt (ใช้ตามนี้ทุกคำ)

บทบาทประจำตำแหน่ง:

```
คุณคือผู้ทดสอบซอฟต์แวร์: ออกแบบเคสทดสอบทั้งปกติ เคสขอบ และเคสผิดพลาด ระบุขั้นตอนและผลที่คาดหวังให้ชัดเจน
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
export AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM=...   # ใส่ key ของคุณเอง
curl -s https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer $AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM" -H 'Content-Type: application/json' \
  -d '{"model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "messages": [{"role":"system","content":"<system prompt จากด้านบน>"},{"role":"user","content":"<คำสั่ง>"}]}'
```

```python
import os, json, urllib.request
req = urllib.request.Request('https://integrate.api.nvidia.com/v1/chat/completions',
    json.dumps({'model': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning', 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': BRIEF}]}).encode(),
    {'Authorization': 'Bearer ' + os.environ['AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM'], 'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content'])
```
