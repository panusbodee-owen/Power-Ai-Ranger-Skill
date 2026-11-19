# vmbridge — let team members drive a virtual Mac

Lets text-only team models use a sandboxed macOS VM run by [terminal-mac](https://github.com/govijr/terminal-mac). The models can't see images, so each step turns the screen into OCR text with coordinates.

## Setup
1. Install terminal-mac, finish its one-time guest setup, then check that `terminal-mac action status` shows both permissions allowed.
2. Build the OCR helper (macOS Vision; reads Thai and English):
   ```
   cd scripts/vmbridge && swiftc -O ocr.swift -o ocr
   ```

## Use
```
python3 bridge.py SEAT "goal" [--steps 15]
```
Each step:
1. Take a screenshot of the VM.
2. Run `ocr` to get `x,y|text` lines.
3. Send them to the team member as a brief.
4. The member replies with one JSON action: click, double, right, type, key, scroll, wait or done.
5. Run that action in the VM.

- **Quota:** each step is one team call, so it counts toward the hourly limit.
- **Output:** `runs/<time>/` holds the screenshot from every step and a `log.json`. The Head must check the screenshots before trusting a "pass".
- **Guards:**
  - Clicks outside the screen are rejected.
  - Text that looks like a password or token is never typed.
  - Scrolling happens at the page margin, so the wheel can't change a focused number field.
- **Scope:** actions only touch the VM, never the host desktop.

---

**ภาษาไทย:** สะพานนี้ให้ลูกทีมใช้ Mac เสมือนได้

แต่ละรอบสะพานจะทำตามนี้:
1. ถ่ายภาพจอ
2. อ่านข้อความบนจอด้วย OCR
3. ส่งข้อความพร้อมตำแหน่งให้ลูกทีม
4. ลูกทีมตอบคำสั่ง JSON กลับมา 1 อัน
5. สะพานสั่งเครื่องเสมือนทำตาม

ทุกรอบนับเป็นการเรียกใช้ 1 ครั้งในโควตารายชั่วโมง

หัวหน้าต้องเปิดดูภาพหน้าจอใน `runs/` ก่อนเชื่อคำว่า "ผ่าน" ของลูกทีม
