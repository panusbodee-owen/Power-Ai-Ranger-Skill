# seat1 — เด็กฝึกงาน

- โมเดล: `nvidia/nemotron-3.5-lightning-30b-a3b`
- Gateway: `integrate.api.nvidia.com` — base URL `https://integrate.api.nvidia.com/v1`
- ตัวแปร key (ตั้งเองในเครื่อง ห้ามใส่ในไฟล์): `AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM`
- ผลเทรน (ชุดทดสอบ B): ก่อนใส่คู่มือ 19/20 → หลัง 18/20 (ข้อผิดพลาดระบบ 0 ครั้ง ไม่นับคะแนน) — ดูรายงานฉบับเต็มใน docs/performance-*.md

## ใช้กับงานแบบไหน

- รวม/สรุปงาน
- ทำตามรูปแบบที่สั่ง
- ตรวจงาน (ตัดสิน pass/fail)
- ผลลัพธ์ JSON ยาว
- เขียนโค้ดสั้น

## อย่าใช้กับงานแบบไหน

- (ไม่พบทักษะที่ต่ำกว่า 50%)

คะแนนทักษะจากงานจริงสะสม (ใช้จัดงานอัตโนมัติ): analysis 0.99 (n=25), code 0.50 (n=0), review 0.50 (n=0), long_output 0.40 (n=0), odoo_spec 0.30 (n=0), test_design 0.25 (n=0)

## System prompt (ใช้ตามนี้ทุกคำ)

บทบาทประจำตำแหน่ง:

```
คุณคือเด็กฝึกงาน: ทำตามโจทย์ทีละขั้นให้ครบ ตอบตรงประเด็นกระชับ ห้ามเดาข้อเท็จจริง ถ้าไม่แน่ใจให้บอกว่าไม่แน่ใจ งานของคุณจะถูกตรวจทุกครั้ง
```

คู่มือสั่งงานที่ได้จากการเทรน (ยังไม่เปิดใช้ในระบบ) — ต่อท้าย system prompt:

```
คำแนะนำการทำงาน (เรียนรู้จากข้อผิดพลาดที่ผ่านมาของคุณ):
- ตอบตรงตามรูปแบบที่สั่งเท่านั้น: ไม่มีคำนำ ไม่มีคำลงท้าย ไม่อธิบายเพิ่มเมื่อไม่ได้ขอ
- ตรวจว่า JSON ถูกต้อง (ปิดวงเล็บครบ ใช้เครื่องหมายคำพูดคู่ ไม่มีคอมม่าเกิน) ก่อนส่ง
```

ที่มา: จากข้อผิดพลาด: json_invalid

## ตัวอย่างการสั่งงาน

**รวม/สรุปงาน**

คำสั่ง:

```
งานย่อย 1) ... 2) ... 3) ...

รวมเป็นสรุปเดียวไม่เกิน 150 ตัวอักษร ต้องมีคำว่า ... (วางคำสั่งไว้ท้ายสุดเสมอ)
```

รูปแบบผลลัพธ์ที่คาดหวัง:

```
สรุปหนึ่งย่อหน้า
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

- คำตอบผิด x2

ข้อควรรู้ทั่วไป: ตัดข้อความระหว่าง `<think>...</think>` ออกก่อนใช้ผลลัพธ์ และตรวจรูปแบบ (JSON) ด้วยโปรแกรมทุกครั้ง อย่าเชื่อว่าโมเดลทำตามรูปแบบเสมอ

## เรียกใช้ทันที

```bash
export AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM=...   # ใส่ key ของคุณเอง
curl -s https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer $AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM" -H 'Content-Type: application/json' \
  -d '{"model": "nvidia/nemotron-3.5-lightning-30b-a3b", "messages": [{"role":"system","content":"<system prompt จากด้านบน>"},{"role":"user","content":"<คำสั่ง>"}]}'
```

```python
import os, json, urllib.request
req = urllib.request.Request('https://integrate.api.nvidia.com/v1/chat/completions',
    json.dumps({'model': 'nvidia/nemotron-3.5-lightning-30b-a3b', 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': BRIEF}]}).encode(),
    {'Authorization': 'Bearer ' + os.environ['AI_TEAM_KEY_INTEGRATE_API_NVIDIA_COM'], 'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content'])
```
