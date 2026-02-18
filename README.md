# Historical Court – Multi-Agent System (Google ADK)

## Overview

โครงงานนี้เป็นการพัฒนา Multi-Agent System ด้วย Google ADK 
เพื่อจำลอง “ศาลประวัติศาสตร์” สำหรับวิเคราะห์บุคคลหรือเหตุการณ์ทางประวัติศาสตร์จากข้อมูลใน Wikipedia

แนวคิดหลักคือการรวบรวมข้อมูลจาก 2 มุมมองที่ขัดแย้งกัน (ด้านบวกและด้านลบ) 
จากนั้นตรวจสอบความสมดุลของหลักฐาน ก่อนสรุปผลเป็นรายงานภาษาไทยและบันทึกเป็นไฟล์ .txt

---

## Architecture Design

ระบบถูกออกแบบตามลำดับขั้นตอนที่กำหนดในโจทย์

### Step 1: Inquiry (Sequential)

Root Agent ทำหน้าที่:
- รับหัวข้อจากผู้ใช้
- เรียก `init_topic()` เพื่อล้างและตั้งค่า session state ใหม่
- ส่งต่อการทำงานไปยัง `court_system` ซึ่งเป็น SequentialAgent

Session state จะถูกสร้างใหม่ทุกครั้งเพื่อป้องกันข้อมูลค้างจากรอบก่อนหน้า

---

### Step 2: Investigation (Parallel)

ใช้ `ParallelAgent` เพื่อให้ 2 Agent ทำงานพร้อมกัน

#### Agent A: Admirer
หน้าที่:
- ค้นหาเฉพาะข้อมูลด้านบวก เช่น ความสำเร็จ นโยบาย ผลงาน หรือ legacy
- ใช้คำค้นเช่น:
  - `{topic}`
  - `{topic} achievements`
  - `{topic} legacy`
- ต้องสร้างข้อมูลจำนวน 3 ข้อ
- ห้ามใช้หน้า Wikipedia ซ้ำ
- บันทึกลงใน `pos_data`
- เก็บชื่อหน้าที่ใช้ไว้ใน `pos_titles_used`

#### Agent B: Critic
หน้าที่:
- ค้นหาเฉพาะข้อมูลด้านลบ ข้อโต้แย้ง คดี หรือประเด็นอื้อฉาว
- ใช้คำค้นเช่น:
  - `{topic} controversy`
  - `{topic} impeachment`
  - `{topic} investigation`
- ต้องสร้างข้อมูล 3 ข้อ โดยใช้ tag ครบดังนี้:
  - FACT[LEGAL]
  - FACT[JAN6]
  - FACT[OTHER]
- บันทึกลงใน `neg_data`
- เก็บชื่อหน้าที่ใช้ไว้ใน `neg_titles_used`

Wiki Research Strategy:
- ใช้ WikipediaAPIWrapper ผ่าน custom tool `wiki_search`
- ดึงทั้ง title และ content
- บังคับอ้างอิงชื่อหน้า (Wikipedia: Page Title)
- ป้องกันการใช้หน้าเดิมซ้ำ

---

### Step 3: Trial & Review (Loop)

ใช้ `LoopAgent` ที่มี Investigation และ Judge อยู่ภายใน

#### Judge Agent ทำหน้าที่:

ตรวจสอบ session state ดังนี้:

1. จำนวนข้อมูลด้านบวก ≥ 3
2. จำนวนข้อมูลด้านลบ ≥ 3
3. ความแตกต่างของจำนวนไม่เกิน 1
4. ฝั่งลบต้องมี tag ครบทั้ง:
   - FACT[LEGAL]
   - FACT[JAN6]
   - FACT[OTHER]

ใช้ tool `check_neg_tags()` เพื่อตรวจสอบ tag อย่างเป็นระบบ

หากข้อมูลยังไม่สมดุล:
- เรียก `set_suffixes()` เพื่อปรับ keyword ให้เจาะจงมากขึ้น
- Loop ทำงานใหม่

หากข้อมูลครบและสมดุล:
- เรียก `exit_loop()` เพื่อจบ loop

เงื่อนไขสำคัญ:
การจบ loop ต้องใช้ `exit_loop` tool เท่านั้น 
ไม่ใช้การตัดสินจาก prompt อย่างเดียว

---

### Step 4: Verdict (Output)

Verdict Writer ทำหน้าที่:

- แปลข้อมูล FACT เป็นภาษาไทย
- แยกหมวดหมู่ด้านบวกและด้านลบ
- คำนวณจำนวน pos_count และ neg_count
- สรุปผลว่า:
  - ถูกมากกว่าผิด
  - ผิดมากกว่าถูก
  - สูสี

ไฟล์จะถูกบันทึกด้วย `write_file()` ที่:

## Limitations

แม้ว่าระบบ Historical Court จะออกแบบให้ค้นหาข้อมูลจากสองมุมมองเพื่อสร้างความเป็นกลาง แต่ยังมีข้อจำกัดบางประการดังนี้

  1. ระบบพึ่งพาข้อมูลจาก Wikipedia เป็นหลัก ซึ่งอาจมีข้อจำกัดด้านความครบถ้วน ความทันสมัย หรืออคติของแหล่งข้อมูล

  2. การกำหนด keyword เช่น controversy หรือ achievements อาจมีผลต่อทิศทางของข้อมูลที่ถูกค้นพบ และอาจไม่ครอบคลุมทุกแง่มุมที่สำคัญ

  3. ระบบจำกัดจำนวนข้อเท็จจริงไว้ที่ 3 ข้อต่อด้าน เพื่อควบคุมโครงสร้างและความสมดุล ซึ่งอาจไม่สะท้อนภาพรวมทั้งหมดของบุคคลหรือเหตุการณ์นั้น
