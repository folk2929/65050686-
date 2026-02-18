import os
import logging
from dotenv import load_dotenv

from google.genai import types
from google.adk import Agent
from google.adk.agents import SequentialAgent, LoopAgent, ParallelAgent
from google.adk.models import Gemini
from google.adk.tools import exit_loop
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.langchain_tool import LangchainTool

from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper

# ----------------------------
# Basic Logging + Env
# ----------------------------
logging.basicConfig(level=logging.INFO)

# (กันพัง) Cloud Logging ใน Qwiklabs บางครั้ง auth มีปัญหา metadata server
# เปิดได้ถ้าอยาก แต่ถ้าแตกจะไม่ทำให้โปรแกรมล้ม
try:
    import google.cloud.logging  # type: ignore
    cloud_logging_client = google.cloud.logging.Client()
    cloud_logging_client.setup_logging()
    logging.info("Cloud Logging enabled.")
except Exception as e:
    logging.warning(f"Cloud Logging disabled (safe fallback): {e}")

load_dotenv()
model_name = os.getenv("MODEL", "gemini-2.5-flash")
logging.info(f"MODEL={model_name}")

RETRY_OPTIONS = types.HttpRetryOptions(initial_delay=1, attempts=6)

wiki_tool = LangchainTool(
    tool=WikipediaQueryRun(
        api_wrapper=WikipediaAPIWrapper(
            lang="en",
            top_k_results=5,
            doc_content_chars_max=4000,
        )
    )
)

# ----------------------------
# Tools
# ----------------------------
def init_topic(tool_context: ToolContext, topic: str) -> dict[str, str]:
    # ล้าง state เก่าทีละ key (State ไม่มี .clear())
    for k in [
        "topic",
        "pos_data", "neg_data",
        "pos_titles_used", "neg_titles_used",
        "pos_suffix", "neg_suffix",
        "required_neg_tags",
    ]:
        try:
            tool_context.state.pop(k, None)
        except Exception:
            pass

    tool_context.state["topic"] = topic.strip()
    tool_context.state["pos_data"] = []
    tool_context.state["neg_data"] = []

    tool_context.state["pos_titles_used"] = []
    tool_context.state["neg_titles_used"] = []

    tool_context.state["pos_suffix"] = " achievements legacy impact reforms diplomacy economy"
    tool_context.state["neg_suffix"] = " controversy impeachment January 6 investigation indictment"

    tool_context.state["required_neg_tags"] = ["FACT[LEGAL]:", "FACT[JAN6]:", "FACT[OTHER]:"]
    return {"status": "success"}

def append_fact(tool_context: ToolContext, key: str, fact: str) -> dict[str, str]:
    existing = tool_context.state.get(key, [])
    if not isinstance(existing, list):
        existing = []

    fact = " ".join((fact or "").split()).strip()

    if key not in ("pos_data", "neg_data"):
        return {"status": "ignored"}

    # ล็อคไม่ให้เกิน 3
    if len(existing) >= 3:
        logging.info(f"[Skipped {key}] (already 3) {fact}")
        return {"status": "skipped"}

    # กันซ้ำ
    if fact in existing:
        logging.info(f"[Skipped {key}] (duplicate) {fact}")
        return {"status": "skipped"}

    existing.append(fact)
    tool_context.state[key] = existing
    logging.info(f"[Added to {key}] {fact}")
    return {"status": "success"}

def check_neg_tags(tool_context: ToolContext) -> dict[str, object]:
    required = tool_context.state.get("required_neg_tags", [])
    neg_data = tool_context.state.get("neg_data", [])

    present = {tag: False for tag in required}
    for line in neg_data:
        for tag in required:
            if isinstance(line, str) and line.startswith(tag):
                present[tag] = True

    ok = all(present.values()) and len(neg_data) == 3
    return {"ok": ok, "present": present, "neg_count": len(neg_data)}

def append_title_used(tool_context: ToolContext, key: str, title: str) -> dict[str, str]:
    """Store used Wikipedia page titles to prevent duplicates."""
    title = " ".join((title or "").split()).strip()
    existing = tool_context.state.get(key, [])
    if not isinstance(existing, list):
        existing = []

    if title and title not in existing:
        existing.append(title)
        tool_context.state[key] = existing
        logging.info(f"[Title added to {key}] {title}")

    return {"status": "success"}

def set_suffixes(tool_context: ToolContext, pos_suffix: str, neg_suffix: str) -> dict[str, str]:
    """Judge refines search keywords for the next loop iteration."""
    tool_context.state["pos_suffix"] = pos_suffix
    tool_context.state["neg_suffix"] = neg_suffix
    logging.info(f"[Suffix updated] pos_suffix={pos_suffix} | neg_suffix={neg_suffix}")
    return {"status": "success"}

def write_file(tool_context: ToolContext, directory: str, filename: str, content: str) -> dict[str, str]:
    """Write final report to disk (safe filename)."""
    os.makedirs(directory, exist_ok=True)

    # กันพัง: ห้าม space/อักขระแปลก
    safe_filename = (
        (filename or "output.txt")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )
    if not safe_filename.endswith(".txt"):
        safe_filename += ".txt"

    target_path = os.path.join(directory, safe_filename)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)

    tool_context.state["output_path"] = target_path
    logging.info(f"[Saved] {target_path}")
    return {"status": "success"}

# ----------------------------
# Agent A: Admirer (Positive)
# ----------------------------
admirer = Agent(
    name="admirer",
    model=Gemini(model=model_name, retry_options=RETRY_OPTIONS),
    description="Collects ONLY positive achievements / legacy from Wikipedia.",
    instruction="""
TOPIC: { topic? }
pos_suffix: { pos_suffix? }
TITLES_USED: { pos_titles_used? }

INSTRUCTIONS:
- You are The Admirer.
- Collect ONLY positive achievements, policies, diplomacy, legacy.
- You MUST call the Wikipedia tool BEFORE writing EACH fact.
- You MUST perform AT LEAST 3 separate Wikipedia tool calls.

SEARCH STRATEGY:
1) "{topic}"
2) "{topic} {pos_suffix}"

TAG DEFINITIONS (STRICT):
- FACT[LEGAL]: MUST be a legal case / indictment / lawsuit / conviction / court ruling / impeachment (legal process).
- FACT[JAN6]: MUST be about January 6 United States Capitol attack OR attempts to overturn the 2020 election.
- FACT[OTHER]: MUST be a major controversy/policy NOT primarily legal AND NOT Jan 6/election overturn.
- FACT[OTHER] MUST NOT be an impeachment and MUST NOT be about Jan 6.

STRICT OUTPUT RULES:

- Produce EXACTLY 3 lines.
- Each line MUST start with: FACT:
- Each line MUST end with: (Wikipedia: Page Title)
- The 3 lines MUST cite 3 DIFFERENT Wikipedia page titles (NO duplicates).
- If you cannot find a new unique Page Title, keep searching until you do.
- DO NOT reuse any page title already listed in TITLES_USED.

FOR EACH FACT:
1) Call Wikipedia tool.
2) Decide the page title used.
3) Call:
   append_title_used(key="pos_titles_used", title="<Page Title>")
4) Then call:
   append_fact(key="pos_data", fact="FACT: ... (Wikipedia: <Page Title>)")

Return ONLY the 3 FACT lines.
No extra commentary.
""",
    tools=[wiki_tool, append_fact, append_title_used],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

# ----------------------------
# Agent B: Critic (Negative)
# ----------------------------
critic_side = Agent(
    name="critic_side",
    model=Gemini(model=model_name, retry_options=RETRY_OPTIONS),
    description="Collects ONLY negative / controversial facts from Wikipedia.",
    instruction="""
TOPIC: { topic? }
neg_suffix: { neg_suffix? }
TITLES_USED: { neg_titles_used? }

INSTRUCTIONS:
- You are The Critic.
- Collect ONLY controversies, legal issues, investigations.
- You MUST call Wikipedia tool BEFORE writing EACH fact.
- You MUST perform AT LEAST 3 separate Wikipedia tool calls.

SEARCH STRATEGY:
1) "{topic} controversy"
2) "{topic} {neg_suffix}"

STRICT OUTPUT RULES:
- Produce EXACTLY 3 lines.
- Use EACH tag EXACTLY ONCE:

  FACT[LEGAL]:
  FACT[JAN6]:
  FACT[OTHER]:

- Each line MUST end with (Wikipedia: Page Title)
- Each line MUST cite a DIFFERENT Wikipedia page title.
- DO NOT reuse page titles in TITLES_USED.
- DO NOT repeat the same event in two categories.

FOR EACH FACT:
1) Call Wikipedia tool.
2) Decide the page title used.
3) Call:
   append_title_used(key="neg_titles_used", title="<Page Title>")
4) Then call:
   append_fact(key="neg_data", fact="<FULL LINE EXACTLY AS WRITTEN>")

Return ONLY the 3 lines.
No extra text.
""",
    tools=[wiki_tool, append_fact, append_title_used],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)



# ----------------------------
# Step 2: Parallel Investigation
# ----------------------------
investigation = ParallelAgent(
    name="investigation",
    description="Runs Admirer and Critic in parallel to collect evidence.",
    sub_agents=[admirer, critic_side],
)

# ----------------------------
# Agent C: Judge (Loop Control)
# ----------------------------
judge = Agent(
    name="judge",
    model=Gemini(model=model_name, retry_options=RETRY_OPTIONS),
    description="Checks evidence balance; refines keywords; ends ONLY via exit_loop.",
    instruction="""
TOPIC: { topic? }

POS_DATA:
{ pos_data? }

NEG_DATA:
{ neg_data? }

INSTRUCTIONS:
1) Compute counts:
   pos_count = number of items in pos_data (0 if empty)
   neg_count = number of items in neg_data (0 if empty)

2) Balanced if ALL:
   - pos_count >= 3
   - neg_count >= 3
   - abs(pos_count - neg_count) <= 1
   - NEG_DATA must contain exactly one line starting with each:
     "FACT[LEGAL]:", "FACT[JAN6]:", "FACT[OTHER]:"

2.5) Call check_neg_tags().
- If check_neg_tags.ok is false, it is NOT balanced.


3) If NOT balanced:
   - Call set_suffixes to refine keywords for next iteration:
     pos_suffix=" achievements presidency policy economy Tax Cuts and Jobs Act USMCA Abraham Accords judicial appointments"
     neg_suffix=" first impeachment Ukraine second impeachment January 6 United States Capitol attack classified documents indictment election interference"
   - Reply briefly: "Not balanced, refining search keywords and continuing."
   - IMPORTANT: Do NOT call exit_loop.

4) If balanced:
   - Call exit_loop tool now.
   - Reply briefly: "Balanced, ending review loop."

RULE:
- The loop MUST end ONLY by calling exit_loop.
""",

    tools=[set_suffixes, check_neg_tags, exit_loop],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

# ----------------------------
# Step 3: Loop = (Investigation -> Judge)
# ----------------------------
trial_loop = LoopAgent(
    name="trial_loop",
    description="Repeats investigation and review until balanced evidence, then exits.",
    sub_agents=[investigation, judge],
    max_iterations=5,
)

# ----------------------------
# Step 4: Verdict Writer
# ----------------------------
verdict_writer = Agent(
    name="verdict_writer",
    model=Gemini(model=model_name, retry_options=RETRY_OPTIONS),
    description="Writes neutral Thai verdict and saves to outputs/<topic>.txt",
    instruction="""
DATA:
TOPIC: { topic? }
POS_DATA: { pos_data? }
NEG_DATA: { neg_data? }

INSTRUCTIONS (STRICT):
- ห้ามตอบเป็น JSON / ห้ามใส่ ``` / ห้ามใส่ code block เด็ดขาด ให้พิมพ์เป็นข้อความล้วนเท่านั้น
- เขียนรายงานภาษาไทยแบบเป็นกลาง
- IMPORTANT: แปลทุกบรรทัดที่ขึ้นต้นด้วย FACT ให้เป็นไทย
- ห้ามคงคำว่า "FACT:" หรือ "FACT[...]" ไว้ในรายงาน ให้แปลเป็น "ข้อเท็จจริง:" แทน
- คงท้ายบรรทัด (Wikipedia: Page Title) ไว้

FORMAT (ต้องมีครบ 1-6):

1) หัวข้อ: <topic>

2) ข้อเท็จจริงด้านบวก
- <แปลเป็นไทย> (Wikipedia: <Page Title>)
- <แปลเป็นไทย> (Wikipedia: <Page Title>)
- <แปลเป็นไทย> (Wikipedia: <Page Title>)

3) ข้อเท็จจริงด้านลบ/ข้อโต้แย้ง
- <แปลเป็นไทย> (Wikipedia: <Page Title>)
- <แปลเป็นไทย> (Wikipedia: <Page Title>)
- <แปลเป็นไทย> (Wikipedia: <Page Title>)

4) ตรวจสมดุล:
- pos_count = <ตัวเลขจริงจากจำนวนข้อใน POS_DATA>
- neg_count = <ตัวเลขจริงจากจำนวนข้อใน NEG_DATA>
- สรุปว่า: <สมดุล/ไม่สมดุล>

5) กติกาตัดสิน:
- ถ้า pos_count > neg_count + 1 => "ถูกมากกว่าผิด"
- ถ้า neg_count > pos_count + 1 => "ผิดมากกว่าถูก"
- นอกนั้น => "สูสี"

6) ข้อสรุปสุดท้าย: <ถูกมากกว่าผิด/ผิดมากกว่าถูก/สูสี> เพราะ <เช่น 3 ต่อ 3>

THEN:
- Call write_file(directory="outputs", filename="{topic}.txt", content=<full report>)

""",
    tools=[write_file],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)

# ----------------------------
# Step 1 + Full Pipeline (Sequential)
# ----------------------------
court_system = SequentialAgent(
    name="court_system",
    description="Historical Court: loop investigation/review then write verdict.",
    sub_agents=[trial_loop, verdict_writer],
)

# ----------------------------
# Root Agent
# ----------------------------
root_agent = Agent(
    name="historical_court_root",
    model=Gemini(model=model_name, retry_options=RETRY_OPTIONS),
    description="Starts Historical Court: ask topic -> init state -> run court_system.",
    instruction="""
ถามผู้ใช้ว่าต้องการให้วิเคราะห์บุคคล/เหตุการณ์ทางประวัติศาสตร์อะไร (พิมพ์อังกฤษจะค้นง่าย)
เมื่อผู้ใช้ตอบ:
- เรียก init_topic(topic=...) เพื่อเซ็ต state
- จากนั้นโอนต่อไปยัง 'court_system'
""",
    tools=[init_topic],
    sub_agents=[court_system],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)