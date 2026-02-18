# Historical Court – Multi-Agent System (Google ADK)

## Overview
ระบบ Multi-Agent จำลอง “ศาลประวัติศาสตร์”  
วิเคราะห์บุคคลหรือเหตุการณ์จาก Wikipedia โดยแยกข้อมูลเป็น 2 ฝั่ง (บวก/ลบ) แล้วสรุปผลอย่างเป็นกลาง พร้อมบันทึกเป็นไฟล์ `.txt`

---

## Architecture

### Step 1: Inquiry (Sequential)
Root Agent:
- รับชื่อหัวข้อจากผู้ใช้
- เรียก `init_topic()` เพื่อตั้งค่า session state
- ส่งต่อไปยัง `court_system`

---

### Step 2: Investigation (Parallel)

ใช้ `ParallelAgent` ทำงาน 2 ฝั่งพร้อมกัน:

**Admirer**
- เก็บข้อมูลด้านบวก
- สร้าง FACT 3 ข้อ
- ห้ามใช้ Wikipedia page ซ้ำ
- บันทึกลง `pos_data`

**Critic**
- เก็บข้อมูลด้านลบ/ข้อโต้แย้ง
- ใช้ tag:
  - FACT[LEGAL]
  - FACT[JAN6]
  - FACT[OTHER]
- สร้าง 3 ข้อ ครบทุก tag
- บันทึกลง `neg_data`

---

### Step 3: Trial & Review (Loop)

Judge ตรวจสอบ:

- pos_count ≥ 3  
- neg_count ≥ 3  
- abs(pos_count - neg_count) ≤ 1  
- negative tags ครบ  

ถ้ายังไม่สมดุล:
- เรียก `set_suffixes()` ปรับ keyword
- ทำ loop ต่อ

ถ้าสมดุล:
- เรียก `exit_loop()` เพื่อจบ loop

⚠ ใช้ tool เท่านั้น ห้ามจบด้วย prompt

---

### Step 4: Verdict (Output)

Verdict Writer:
- แปล FACT เป็นภาษาไทย
- จัดหมวดหมู่บวก/ลบ
- คำนวณจำนวน
- สรุปผลว่า:
  - ถูกมากกว่าผิด
  - ผิดมากกว่าถูก
  - สูสี
- บันทึกไฟล์ `outputs/<topic>.txt`

---

## State Management

Session State ที่ใช้:

- topic
- pos_data
- neg_data
- pos_titles_used
- neg_titles_used
- pos_suffix
- neg_suffix
- required_neg_tags

ใช้ templating เช่น:
