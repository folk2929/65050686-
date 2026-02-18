# Historical Court – Multi-Agent System (Google ADK)

## Overview
Multi-Agent System จำลอง “ศาลประวัติศาสตร์” เพื่อวิเคราะห์บุคคล/เหตุการณ์ โดยดึงหลักฐานจาก Wikipedia แบบ 2 ฝั่ง (บวก/ลบ) แล้วสรุปผลเป็นกลาง พร้อมบันทึกเป็นไฟล์ .txt

## Architecture (ADK Agents)

### Step 1: Inquiry (Root → Sequential)
- Root Agent รับหัวข้อจากผู้ใช้ (แนะนำให้พิมพ์อังกฤษเพื่อค้นง่าย)
- เรียก `init_topic(topic=...)` เพื่อ init/clear session state
- ส่งต่อไปยัง `court_system` (SequentialAgent)

### Step 2: Investigation (Parallel)
ทำงานพร้อมกันด้วย ParallelAgent:
- **Agent A: Admirer** → รวบรวม “ด้านบวก/ความสำเร็จ/legacy”
- **Agent B: Critic** → รวบรวม “ด้านลบ/ข้อโต้แย้ง/คดี/ประเด็นอื้อฉาว”

**Wiki Research Strategy**
- Admirer ใช้คำค้นแนวบวก เช่น `{topic} achievements / legacy / policy ...`
- Critic ใช้คำค้นแนวลบ เช่น `{topic} controversy / impeachment / investigation ...`
- เพื่อกันข้อมูลซ้ำ เก็บรายชื่อหน้าที่ใช้แล้วไว้ใน state (`pos_titles_used`, `neg_titles_used`)

### Step 3: Trial & Review (Loop)
- **Agent C: Judge** ตรวจสมดุลของหลักฐานใน state (`pos_data`, `neg_data`)
- หากยังไม่สมดุล Judge จะ “ปรับ keyword” (`set_suffixes`) แล้วให้ loop ค้นใหม่
- **จบ loop ได้ด้วย `exit_loop` tool เท่านั้น** เมื่อหลักฐานครบและสมดุลตามเงื่อนไข

### Step 4: Verdict (Output)
- Verdict Writer สรุปรายงานภาษาไทยแบบเป็นกลาง
- บันทึกเป็นไฟล์ `outputs/<topic>.txt` ผ่าน `write_file`

## State Management (Session State)
Keys ที่ใช้ใน session state:
- `topic`
- `pos_data`, `neg_data`
- `pos_titles_used`, `neg_titles_used`
- `pos_suffix`, `neg_suffix`
- `required_neg_tags`
- `output_path` (path ไฟล์ output)

## Tools
- Wikipedia Tool: LangChain `WikipediaQueryRun` (ผ่าน `LangchainTool`)
- Loop control: `exit_loop`
- Custom tools:
  - `init_topic`
  - `append_fact`
  - `append_title_used`
  - `check_neg_tags`
  - `set_suffixes`
  - `write_file`

## Output
ไฟล์ผลลัพธ์อยู่ที่ `outputs/<topic>.txt` มี:
- ข้อเท็จจริงด้านบวก (อ้างอิง Wikipedia: Page Title)
- ข้อเท็จจริงด้านลบ/ข้อโต้แย้ง (อ้างอิง Wikipedia: Page Title)
- การตรวจสมดุล + ข้อสรุปสุดท้าย
