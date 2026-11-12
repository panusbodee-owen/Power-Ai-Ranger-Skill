# คู่มือสั่งงานทีม AI (Power AI Ranger)

ไฟล์นี้และไฟล์รายคนในโฟลเดอร์เดียวกันสร้างจากฐานข้อมูลของทีมด้วย `python3 scripts/train.py guide docs/team` สร้างใหม่ได้หลังเทรนซ้ำ ไม่มี API key อยู่ในไฟล์ใดๆ

## ใช้บนเครื่อง/เซสชันอื่น

1. clone repo นี้ แล้วอ่านไฟล์ของสมาชิกที่ต้องการ (`docs/team/<ชื่อ>.md`)
2. ตั้ง key ใน environment ตามชื่อตัวแปรที่ระบุในไฟล์ (ห้ามใส่ key ลงไฟล์หรือ repo)
3. เรียกผ่าน curl/Python ตามตัวอย่างในไฟล์ หรือใช้ `scripts/team.py` ถ้าตั้ง gateway/seat ไว้แล้ว
4. เซสชัน Claude ใหม่: ให้อ่าน `docs/TEAM-GUIDE.md` กับไฟล์รายคนก่อนสั่งงาน แล้วใช้ system prompt + คู่มือ (coaching) ตามที่ระบุตัวต่อตัว

## สมาชิก

| ชื่อ | บทบาท | โมเดล | ถนัด | ผลเทรน (ก่อน→หลัง) |
|---|---|---|---|---|
| [worker](team/worker.md) | ผู้ตรวจสำรอง / งานสั้น | `deepseek-ai/deepseek-v4-flash-0731` | ดึงข้อมูลเป็น JSON, ตอบสั้น/คำนวณ, รวม/สรุปงาน | 20/20 → 20/20 |
| [intern](team/intern.md) | ผู้ทดสอบ | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | ดึงข้อมูลเป็น JSON, ตอบสั้น/คำนวณ, รวม/สรุปงาน | 20/20 → 20/20 |
| [seat3](team/seat3.md) | ผู้ช่วย Senior PD (Groq 120B) | `openai/gpt-oss-120b` | ดึงข้อมูลเป็น JSON, ตอบสั้น/คำนวณ, รวม/สรุปงาน | 17/20 → 20/20 |
| [seat4](team/seat4.md) | สำรอง (Groq) | `qwen/qwen3.8-27b` | ตอบสั้น/คำนวณ, รวม/สรุปงาน, ทำตามรูปแบบที่สั่ง | 18/20 → 18/20 |
| [seat1](team/seat1.md) | เด็กฝึกงาน | `nvidia/nemotron-3.5-lightning-30b-a3b` | รวม/สรุปงาน, ทำตามรูปแบบที่สั่ง, ตรวจงาน (ตัดสิน pass/fail) | 19/20 → 18/20 |
| [seat2](team/seat2.md) | Senior Product Designer (หลัก) | `nvidia/nemotron-3-ultra-550b-a55b` | ดึงข้อมูลเป็น JSON, ตอบสั้น/คำนวณ, รวม/สรุปงาน | 18/20 → 20/20 |
| [seat5](team/seat5.md) | ผู้ตรวจหลัก | `unsloth/qwen3.8-27b-nvfp4` | ดึงข้อมูลเป็น JSON, ตอบสั้น/คำนวณ, รวม/สรุปงาน | 19/20 → 20/20 |
