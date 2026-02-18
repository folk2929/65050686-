# Historical Court – Multi-Agent System (Google ADK)

## Overview
ระบบ Multi-Agent สำหรับวิเคราะห์บุคคล/เหตุการณ์ทางประวัติศาสตร์
โดยแยกข้อมูลด้านบวกและด้านลบจาก Wikipedia
แล้วสรุปผลแบบเป็นกลาง

## Architecture

### Step 1: Inquiry (Sequential)
รับชื่อหัวข้อจากผู้ใช้

### Step 2: Investigation (Parallel)
- Agent A: Admirer → ค้นหาด้านบวก
- Agent B: Critic → ค้นหาด้านลบ

### Step 3: Trial & Review (Loop)
Judge ตรวจสอบ state:
- pos_data
- neg_data

หากข้อมูลไม่สมดุล → สั่งค้นหาใหม่
จบ loop ด้วย exit_loop tool เท่านั้น

### Step 4: Verdict
สรุปผลและบันทึกเป็น .txt

## State Management
ใช้ session state:
- topic
- pos_data
- neg_data

## Tools
- wikipedia search
- exit_loop
- write_file
