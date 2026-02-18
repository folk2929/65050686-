import os
import logging
import re
from dotenv import load_dotenv

from google.genai import types
from google.adk import Agent
from google.adk.agents import SequentialAgent, LoopAgent, ParallelAgent
from google.adk.models import Gemini
from google.adk.tools import exit_loop
from google.adk.tools.tool_context import ToolContext

from langchain_community.utilities import WikipediaAPIWrapper


# ----------------------------
# Basic Logging + Env
# ----------------------------
logging.basicConfig(level=logging.INFO)

# (กันพัง) Cloud Logging ใน Qwiklabs บางครั้ง auth มีปัญหา metadata server
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

# Wikipedia wrapper (คืน title ได้จริง)
wiki_api = WikipediaAPIWrapper(
    lang="en",
    top_k_results=5,
    doc_content_chars_max=4000,
)


# ----------------------------
# Tools
# ----------------------------
def init_topic(tool_context: ToolContext, topic: str) -> dict[str, str]:
    """Initialize state for a new topic."""
    # ล้าง state เก่าทีละ key (State ไม่มี .clear())
    for k in [
        "topic",
        "pos_data", "neg_data",
        "pos_titles_used", "neg_titles_used",
        "pos_suffix", "neg_suffix",
        "required_neg_tags",
        "output_path",
    ]:
        try:
            tool_context.state.pop(k, None)
        except Exception:
            pass

    tool_context.state["topic"] = (topic or "").strip()
    tool_context.state["pos_data"] = []
    tool_context.state["neg_data"] = []

    tool_context.state["pos_titles_used"] = []
    tool_context.state["neg_titles_used"] = []

    tool_context.state["pos_suffix"] = " achievements legacy impact reforms diplomacy economy"
    tool_context.state["neg_suffix"] = " controversy impeachment January 6 investigation indictment"

    tool_context.state["required_neg_tags"] = ["FACT[LEGAL]:", "FACT[JAN6]:", "FACT[OTHER]:"]
    return {"status": "success"}


def wiki_search(tool_context: ToolContext, query: str) -> dict[str, str]:
    """
    Search Wikipedia and return a single best page title + content snippet.
    Return schema:
      { ok: "true"/"false", title: str, content: str }
    """
    q = " ".join((query or "").split()).strip()
    if not q:
        return {"ok": "false", "title": "", "content": ""}

    try:
        docs = wiki_api.load(q)
    except Exception as e:
        logging.warning(f"[wiki_search error] {e}")
        return {"ok": "false", "title": "", "content": ""}

    if not docs:
        return {"ok": "false", "title": "", "content": ""}

    d0 = docs[0]
    title = ""
    try:
        title = (d0.metadata or {}).get("title", "") or ""
    except Exception:
        title = ""

    content = (d0.page_content or "").strip()
    return {"ok": "true", "title": title, "content": content}


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


def append_fact(tool_context: ToolContext, key: str, fact: str) -> dict:
    """Append fact(s) into pos_data / neg_data with dedup and cap=3.
    If model returns multiple FACT lines in one blob, split and store them.
    """
    if key not in ("pos_data", "neg_data"):
        return {"status": "ignored"}

    data = tool_context.state.get(key, [])
    if not isinstance(data, list):
        data = []

    blob = (fact or "").strip()
    if not blob:
        return {"status": "empty"}

    # --- Split strategy ---
    lines = []

    # 1) Normal newline split
    for ln in blob.splitlines():
        ln = " ".join(ln.split()).strip()
        if ln:
            lines.append(ln)

    # 2) If still looks like combined FACTs in one line, split by tags/prefix
    if len(lines) == 1:
        one = lines[0]
        if key == "neg_data" and ("FACT[LEGAL]:" in one and "FACT[JAN6]:" in one and "FACT[OTHER]:" in one):
            parts = re.split(r"(?=FACT\[(?:LEGAL|JAN6|OTHER)\]:)", one)
            lines = [p.strip() for p in parts if p.strip()]
        elif key == "pos_data" and one.count("FACT:") >= 2:
            parts = re.split(r"(?=FACT:)", one)
            lines = [p.strip() for p in parts if p.strip()]

    added = 0
    for ln in lines:
        if len(data) >= 3:
            break
        if ln not in data:
            data.append(ln)
            added += 1

    tool_context.state[key] = data
    return {"status": "ok", "added": added, "count": len(data)}


def check_neg_tags(tool_context: ToolContext) -> dict[str, object]:
    """Validate negative tags presence and count."""
    required = tool_context.state.get("required_neg_tags", [])
    neg_data = tool_context.state.get("neg_data", [])

    present = {tag: False for tag in required}
    for line in neg_data:
        for tag in required:
            if isinstance(line, str) and line.startswith(tag):
                present[tag] = True

    ok = all(present.values()) and isinstance(neg_data, list) and len(neg_data) == 3
    return {"ok": ok, "present": present, "neg_count": len(neg_data) if isinstance(neg_data, list) else 0}


def set_suffixes(tool_context: ToolContext, pos_suffix: str, neg_suffix: str) -> dict[str, str]:
    """Judge refines search keywords for the next loop iteration."""
    tool_context.state["pos_suffix"] = pos_suffix
    tool_context.state["neg_suffix"] = neg_suffix
    logging.info(f"[Suffix updated] pos_suffix={pos_suffix} | neg_suffix={neg_suffix}")
    return {"status": "success"}


def write_file(tool_context: ToolContext, directory: str, filename: str, content: str) -> dict[str, str]:
    """Write final report to disk using a safe filename."""
    os.makedirs(directory, exist_ok=True)

    raw = filename or "output.txt"
    raw = raw.replace(" ", "_")
    # เก็บไว้แค่ a-z A-Z 0-9 _ - . (กันพังทุก OS)
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", raw)
    if not safe.endswith(".txt"):
        safe += ".txt"

    target_path = os.path.join(directory, safe)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content or "")

    tool_context.state["output_path"] = target_path
    logging.info(f"[Saved] {target_path}")
    return {"status": "success", "path": target_path}


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
- You MUST call wiki_search BEFORE writing EACH fact.
- You MUST perform AT LEAST 3 separate wiki_search calls.

SEARCH STRATEGY (try in order, until you get a NEW page title):
1) "{topic}"
2) "{topic} {pos_suffix}"
3) "{topic} presidency achievements"
4) "{topic} legacy"

CITATION RULE (IMPORTANT):
- After EACH Wikipedia search, you MUST cite the Wikipedia page title you used by ending the fact line with:
  (Wikipedia: <Page Title>)

STRICT OUTPUT RULES:
- Produce EXACTLY 3 lines.
- Each line MUST start with: FACT:
- Each line MUST end with: (Wikipedia: Page Title)
- The 3 lines MUST cite 3 DIFFERENT Wikipedia page titles (NO duplicates).
- DO NOT reuse any page title already listed in TITLES_USED.

FOR EACH FACT (MANDATORY WORKFLOW):
1) Call: wiki_search(query="...").
2) From result, take title=result.title (must be non-empty).
3) If title is empty OR title is already in TITLES_USED -> search again with another query.
4) Call: append_title_used(key="pos_titles_used", title=title)
5) Write ONE short positive fact from result.content.
6) Call: append_fact(key="pos_data", fact="FACT: ... (Wikipedia: <title>)")

Return ONLY the 3 lines. No extra text.
""",
    tools=[wiki_search, append_fact, append_title_used],
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
- Collect ONLY controversies, legal issues, investigations, major disputes.
- You MUST call wiki_search BEFORE writing EACH fact.
- You MUST perform AT LEAST 3 separate wiki_search calls.

SEARCH STRATEGY (try in order, until you get a NEW page title):
1) "{topic} controversy"
2) "{topic} {neg_suffix}"
3) "{topic} impeachment"
4) "{topic} investigation"
5) "{topic} January 6 United States Capitol attack"

CITATION RULE (IMPORTANT):
- After EACH Wikipedia search, you MUST cite the Wikipedia page title you used by ending the fact line with:
  (Wikipedia: <Page Title>)

STRICT OUTPUT RULES:
- Produce EXACTLY 3 lines.
- Use EACH tag EXACTLY ONCE (no more, no less), and in any order:
  FACT[LEGAL]:
  FACT[JAN6]:
  FACT[OTHER]:

- Each line MUST end with (Wikipedia: Page Title)
- Each line MUST cite a DIFFERENT Wikipedia page title.
- DO NOT reuse page titles in TITLES_USED.
- DO NOT repeat the same event in two categories.

TAG MEANINGS (STRICT):
- FACT[LEGAL]: legal case / indictment / lawsuit / conviction / court ruling / impeachment process.
- FACT[JAN6]: January 6 United States Capitol attack OR attempts to overturn the 2020 election.
- FACT[OTHER]: major controversy/policy NOT primarily legal AND NOT Jan 6/election overturn.

FOR EACH FACT (MANDATORY WORKFLOW):
1) Call: wiki_search(query="...").
2) From result, take title=result.title (must be non-empty).
3) If title is empty OR title is already in TITLES_USED -> search again with another query.
4) Call: append_title_used(key="neg_titles_used", title=title)
5) Write ONE short negative fact from result.content that matches the tag category.
6) Call: append_fact(key="neg_data", fact="<FULL LINE EXACTLY AS WRITTEN>")

Return ONLY the 3 lines. No extra text.
""",
    tools=[wiki_search, append_fact, append_title_used],
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

2) Call check_neg_tags().
- If check_neg_tags.ok is false, it is NOT balanced.

3) Balanced if ALL:
   - pos_count >= 3
   - neg_count >= 3
   - abs(pos_count - neg_count) <= 1
   - check_neg_tags.ok == true

4) If NOT balanced:
   - Call set_suffixes to refine keywords for next iteration:
     pos_suffix=" achievements presidency policy economy diplomacy reforms legacy"
     neg_suffix=" controversy impeachment January 6 United States Capitol attack investigation indictment election interference"
   - Reply briefly: "Not balanced, refining search keywords and continuing."
   - IMPORTANT: Do NOT call exit_loop.

5) If balanced:
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
5) กติกาตัดสิน:
- ถ้า pos_count > neg_count => "ถูกมากกว่าผิด"
- ถ้า neg_count > pos_count => "ผิดมากกว่าถูก"
- ถ้า pos_count == neg_count => "สูสี"
6) ข้อสรุปสุดท้าย: <ถูกมากกว่าผิด/ผิดมากกว่าถูก/สูสี> เพราะ <เช่น 3 ต่อ 2>

FILE SAVE (MANDATORY):
- หลังจากพิมพ์รายงานครบทั้ง 1-6 ให้เรียก write_file
- โดยให้ content = ข้อความรายงานทั้งหมด (ทั้ง 1-6) แบบตรงตัว

Call:
write_file(directory="outputs", filename="{topic}.txt", content="<FULL REPORT TEXT>")

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
    description="Starts Historical Court: init state -> run court_system.",
    instruction="""
RULE:
- If state.topic is missing, treat the user's latest message as the topic (English preferred).
- If user message is empty or greeting-only, ask again for an English topic.

FLOW:
1) If {topic?} is empty:
   - call init_topic(topic="<user_message>")
2) then run court_system
""",
    tools=[init_topic],
    sub_agents=[court_system],
    generate_content_config=types.GenerateContentConfig(temperature=0),
)
