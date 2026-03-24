
import os
import re
import uuid
import sqlite3
import textwrap
from io import BytesIO
from datetime import datetime

import pdfplumber
from flask import (
    Flask, request, jsonify, render_template_string,
    redirect, url_for, session, send_file
)
from werkzeug.utils import secure_filename
from openai import OpenAI
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

UPLOAD_FOLDER = "uploads"
DB_PATH = "legal_advisor.db"
MAX_CONTENT_MB = 16
ALLOWED_EXT = {"pdf", "txt"}
PORT = 5000

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "india-legal-advisor-secret-2026"
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_MB * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ─────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────
def column_exists(cur, table_name, column_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = cur.fetchall()
    return any(col[1] == column_name for col in cols)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            title TEXT,
            language TEXT DEFAULT 'en',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            analysis TEXT,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration support for old DBs
    if not column_exists(cur, "sessions", "language"):
        cur.execute("ALTER TABLE sessions ADD COLUMN language TEXT DEFAULT 'en'")

    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content)
    )
    cur.execute(
        "INSERT OR IGNORE INTO sessions (session_id, title, language) VALUES (?, ?, ?)",
        (session_id, f"Legal Session {session_id[:8]}", "en")
    )
    conn.commit()
    conn.close()

def get_session_messages(session_id, limit=100):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC LIMIT ?",
        (session_id, limit)
    )
    rows = cur.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]

def get_all_sessions():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if column_exists(cur, "sessions", "language"):
        cur.execute("""
            SELECT s.session_id, s.title, COALESCE(s.language, 'en'), s.created_at, COUNT(m.id) as msg_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id, s.title, s.language, s.created_at
            ORDER BY s.created_at DESC
            LIMIT 30
        """)
    else:
        cur.execute("""
            SELECT s.session_id, s.title, 'en', s.created_at, COUNT(m.id) as msg_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id, s.title, s.created_at
            ORDER BY s.created_at DESC
            LIMIT 30
        """)

    rows = cur.fetchall()
    conn.close()

    return [
        {"id": r[0], "title": r[1] or "Legal Consultation", "language": r[2], "created": r[3], "count": r[4]}
        for r in rows
    ]

def save_document_analysis(filename, analysis):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO documents (filename, analysis) VALUES (?, ?)",
        (filename, analysis)
    )
    conn.commit()
    conn.close()

def get_recent_documents(limit=5):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT filename, analysis, uploaded_at FROM documents ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return [{"filename": r[0], "analysis": r[1], "uploaded": r[2]} for r in rows]

def update_session_language(session_id, language):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO sessions (session_id, title, language) VALUES (?, ?, ?)",
        (session_id, f"Legal Session {session_id[:8]}", language)
    )
    cur.execute(
        "UPDATE sessions SET language=? WHERE session_id=?",
        (language, session_id)
    )
    conn.commit()
    conn.close()

def update_session_title(session_id, title):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET title=? WHERE session_id=?", (title, session_id))
    conn.commit()
    conn.close()

init_db()

# ─────────────────────────────────────────────────────────────
# GROQ CLIENT
# ─────────────────────────────────────────────────────────────
client = None
if GROQ_API_KEY:
    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1"
    )

# ─────────────────────────────────────────────────────────────
# LEGAL DATA
# ─────────────────────────────────────────────────────────────
LEGAL_QA_DATASET = [
    {
        "question": "What is IPC Section 302?",
        "answer": "IPC Section 302 deals with punishment for murder. Punishment may be death or imprisonment for life, and fine."
    },
    {
        "question": "What is IPC Section 420?",
        "answer": "IPC Section 420 deals with cheating and dishonestly inducing delivery of property. Punishment can extend to 7 years and fine."
    },
    {
        "question": "What is anticipatory bail?",
        "answer": "Anticipatory bail under Section 438 CrPC is pre-arrest bail granted by Sessions Court or High Court in non-bailable offences."
    },
    {
        "question": "What is Section 138 of NI Act?",
        "answer": "Section 138 of the Negotiable Instruments Act deals with cheque bounce due to insufficient funds."
    },
    {
        "question": "What is RTI Act?",
        "answer": "The RTI Act 2005 allows citizens to seek information from public authorities, generally within 30 days."
    },
    {
        "question": "What is domestic violence law in India?",
        "answer": "Protection of Women from Domestic Violence Act, 2005 provides civil remedies like protection orders, residence rights, and monetary relief."
    },
    {
        "question": "What is FIR?",
        "answer": "FIR means First Information Report. It is recorded by police under criminal procedure when information about a cognizable offence is given."
    },
    {
        "question": "What is BNS?",
        "answer": "BNS means Bharatiya Nyaya Sanhita, which replaced the Indian Penal Code in the new criminal law framework."
    }
]

IPC_BNS_DATA = {
    "302": {
        "old_law": "IPC Section 302",
        "new_law": "BNS equivalent may apply depending on offence mapping",
        "topic": "Murder",
        "punishment": "Death or imprisonment for life and fine",
        "bailable": "No",
        "cognizable": "Yes"
    },
    "420": {
        "old_law": "IPC Section 420",
        "new_law": "Use corresponding BNS cheating provision if applicable",
        "topic": "Cheating",
        "punishment": "Up to 7 years and fine",
        "bailable": "Usually No/depends on exact classification and facts",
        "cognizable": "Yes"
    },
    "498A": {
        "old_law": "IPC Section 498A",
        "new_law": "Use relevant BNS provision where applicable",
        "topic": "Cruelty by husband or relatives",
        "punishment": "Up to 3 years and fine",
        "bailable": "No",
        "cognizable": "Yes"
    },
    "138": {
        "old_law": "NI Act Section 138",
        "new_law": "Negotiable Instruments Act remains relevant",
        "topic": "Cheque Bounce",
        "punishment": "Up to 2 years or fine up to twice cheque amount or both",
        "bailable": "Usually bailable",
        "cognizable": "Generally complaint-based process"
    }
}

EMERGENCY_CONTACTS = [
    {"name": "Police Emergency", "number": "112"},
    {"name": "Women Helpline", "number": "181"},
    {"name": "Child Helpline", "number": "1098"},
    {"name": "Cyber Crime Helpline", "number": "1930"},
    {"name": "Ambulance", "number": "108"},
]

def build_legal_context():
    lines = ["LEGAL KNOWLEDGE BASE (Indian Law):", "=" * 50]
    for item in LEGAL_QA_DATASET:
        lines.append(f"Q: {item['question']}")
        lines.append(f"A: {item['answer']}")
        lines.append("")
    return "\n".join(lines)

LEGAL_CONTEXT = build_legal_context()

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
LANGUAGE_MAP = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi"
}

LANGUAGE_NATIVE = {
    "en": "English",
    "hi": "हिंदी",
    "mr": "मराठी"
}

def get_session_id():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def extract_text_from_pdf(path: str) -> str:
    try:
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(extracted)
        result = "\n".join(text_parts)
        return result if result.strip() else "Could not extract text from PDF."
    except Exception as e:
        return f"PDF read error: {str(e)}"

def extract_text_from_txt(path: str) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return "Could not decode text file."

def extract_text(path: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[1].lower()
    if ext == "pdf":
        return extract_text_from_pdf(path)
    if ext == "txt":
        return extract_text_from_txt(path)
    return "Unsupported file type."

def guess_title_from_message(message: str) -> str:
    msg = message.strip()
    if not msg:
        return "Legal Consultation"
    msg = re.sub(r"\s+", " ", msg)
    return msg[:50] + ("..." if len(msg) > 50 else "")

def get_language_instruction(language_code: str) -> str:
    lang = LANGUAGE_MAP.get(language_code, "English")
    return f"""
Reply only in {lang}.
If the user writes in Hindi, Marathi, or English, understand it and answer in {lang}.
Use simple language.
Always format the answer with clear headings:
1. Relevant law/section
2. Plain explanation
3. Immediate next steps
4. Important documents/evidence
5. A short caution/disclaimer
"""

def build_system_prompt(language_code: str) -> str:
    return f"""
You are an expert AI Legal Advisor specialised in Indian law.

Your knowledge covers:
- IPC / BNS
- CrPC / BNSS
- Constitution of India
- Negotiable Instruments Act
- Consumer Protection Act
- Motor Vehicles Act
- Hindu Marriage Act
- Indian Evidence Act / BSA
- RTI Act
- Domestic Violence law
- General legal procedure in India

{LEGAL_CONTEXT}

INSTRUCTIONS:
- Explain legal concepts in plain language.
- Mention specific sections/acts where possible.
- If law has shifted from IPC/CrPC to BNS/BNSS, mention both when helpful.
- Never claim to be a lawyer.
- Give practical next steps.
- Keep answers structured with headings and bullets when needed.
- Avoid one big paragraph.
- End with a brief disclaimer.

{get_language_instruction(language_code)}
"""

def safe_model_reply(user_message: str, history: list, language_code: str) -> str:
    if not client:
        return (
            "Groq API key not found.\n\n"
            "In PowerShell run:\n"
            '$env:GROQ_API_KEY="your_new_key_here"\n'
            "python app.py"
        )

    messages = [{"role": "system", "content": build_system_prompt(language_code)}]
    for turn in history[-10:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1200,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"API error: {str(e)}"

def analyze_document(text: str, filename: str, language_code: str) -> str:
    if not client:
        return "Groq API key not found."

    prompt = f"""
Analyse the following legal document under Indian law.

Provide:
1. Document Type
2. Key Parties
3. Summary
4. Important Clauses / Legal Points
5. Relevant Laws / Sections
6. Risk Flags
7. Recommended Next Steps
8. Disclaimer

Reply in {LANGUAGE_MAP.get(language_code, 'English')}.
Use clear headings and structured formatting.

DOCUMENT NAME: {filename}

DOCUMENT TEXT:
{text[:12000]}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": build_system_prompt(language_code)},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1400,
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Document analysis error: {str(e)}"

def generate_fir_guidance(data, language_code):
    incident = data.get("incident_type", "")
    date = data.get("incident_date", "")
    place = data.get("incident_place", "")
    accused = data.get("accused_known", "")
    evidence = data.get("evidence", "")
    police_status = data.get("police_status", "")

    prompt = f"""
User needs FIR guidance in {LANGUAGE_MAP.get(language_code, 'English')}.

Details:
- Incident type: {incident}
- Date: {date}
- Place: {place}
- Is accused known: {accused}
- Available evidence: {evidence}
- Police complaint status: {police_status}

Provide a well-structured answer with these headings:
1. FIR Relevance
2. Possible Legal Sections
3. Facts to Mention
4. Documents / Evidence to Keep Ready
5. Where to Go
6. Immediate Next Steps
7. Important Precautions
8. Disclaimer
"""
    return safe_model_reply(prompt, [], language_code)

def generate_legal_draft(data, language_code):
    draft_type = data.get("draft_type", "")
    name = data.get("name", "")
    opponent = data.get("opponent", "")
    facts = data.get("facts", "")
    relief = data.get("relief", "")

    prompt = f"""
Draft a simple {draft_type} under Indian legal context.

Language: {LANGUAGE_MAP.get(language_code, 'English')}

Details:
- Sender name: {name}
- Opposite party / authority: {opponent}
- Facts: {facts}
- Requested relief/action: {relief}

Make it professional, simple, well-formatted, and usable as a first draft.
Use a proper subject line, greeting, body, prayer/request, and closing.
Include placeholders where needed.
Add a short disclaimer at the end.
"""
    return safe_model_reply(prompt, [], language_code)

def make_text_pdf(title, content):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left_margin = 50
    top = height - 50
    y = top

    p.setTitle(title)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(left_margin, y, title)
    y -= 25

    p.setFont("Helvetica", 10)
    wrapped_lines = []
    for para in content.splitlines():
        if not para.strip():
            wrapped_lines.append("")
        else:
            wrapped_lines.extend(textwrap.wrap(para, width=95))

    for line in wrapped_lines:
        if y < 50:
            p.showPage()
            p.setFont("Helvetica", 10)
            y = height - 50
        p.drawString(left_margin, y, line[:110])
        y -= 14

    p.save()
    buffer.seek(0)
    return buffer

# ─────────────────────────────────────────────────────────────
# TEMPLATES
# ─────────────────────────────────────────────────────────────
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Legal Advisor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
--bg:#0f1117;--surface:#171b24;--surface2:#212836;--border:#30384a;
--text:#e8edf7;--muted:#a8b3c7;--accent:#6c63ff;--accent2:#00c2a8;
--gold:#f5c842;--danger:#ef4444;--success:#10b981;--radius:14px;
}
body{
font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--text);
display:flex;min-height:100vh;
}
.sidebar{
width:270px;background:var(--surface);border-right:1px solid var(--border);
padding:20px;display:flex;flex-direction:column;gap:10px;overflow-y:auto
}
.logo{
padding-bottom:18px;border-bottom:1px solid var(--border);margin-bottom:6px
}
.logo h2{color:var(--gold);font-size:20px}
.logo p{color:var(--muted);font-size:12px;margin-top:4px}
.nav-link{
display:block;padding:11px 14px;border-radius:10px;text-decoration:none;
color:var(--muted);background:transparent
}
.nav-link:hover,.nav-link.active{background:var(--surface2);color:var(--text)}
.section-title{
font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;
margin-top:10px
}
.session-item{
display:flex;justify-content:space-between;gap:8px;padding:9px 12px;
text-decoration:none;color:var(--muted);border-radius:10px;font-size:12px
}
.session-item:hover{background:var(--surface2);color:var(--text)}
.badge{
background:var(--accent);color:#fff;padding:2px 8px;border-radius:999px;font-size:11px
}
.disclaimer{
margin-top:auto;background:rgba(245,200,66,.08);border:1px solid rgba(245,200,66,.25);
padding:12px;border-radius:10px;color:var(--gold);font-size:12px;line-height:1.5
}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.header{
padding:18px 24px;background:var(--surface);border-bottom:1px solid var(--border);
display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap
}
.header h1{font-size:20px}
.header p{font-size:13px;color:var(--muted);margin-top:4px}
.btn{
border:none;border-radius:10px;padding:10px 14px;cursor:pointer;text-decoration:none;
display:inline-flex;align-items:center;gap:8px;font-size:13px
}
.btn-primary{background:var(--accent);color:#fff}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
.content{flex:1;overflow:auto}
.card{
background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px
}
input,select,textarea{
width:100%;background:var(--surface2);border:1px solid var(--border);color:var(--text);
padding:12px 14px;border-radius:10px;outline:none
}
textarea{resize:vertical;min-height:100px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.small{font-size:12px;color:var(--muted)}
.result-box{
white-space:pre-wrap;word-break:break-word;background:var(--surface2);
padding:16px;border-radius:12px;border:1px solid var(--border);line-height:1.7
}
.action-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
</style>
{% block extra_styles %}{% endblock %}
</head>
<body>
<aside class="sidebar">
  <div class="logo">
    <h2>⚖️ AI Legal Advisor</h2>
    <p>Indian Law · English / हिंदी / मराठी</p>
  </div>

  <a class="nav-link {{ 'active' if active_page == 'chat' else '' }}" href="/">💬 Chat</a>
  <a class="nav-link {{ 'active' if active_page == 'docs' else '' }}" href="/documents">📄 Document Analysis</a>
  <a class="nav-link {{ 'active' if active_page == 'fir' else '' }}" href="/fir-wizard">📝 FIR Wizard</a>
  <a class="nav-link {{ 'active' if active_page == 'draft' else '' }}" href="/draft-generator">📨 Draft Generator</a>
  <a class="nav-link {{ 'active' if active_page == 'lookup' else '' }}" href="/ipc-bns-lookup">📚 IPC/BNS Lookup</a>
  <a class="nav-link {{ 'active' if active_page == 'emergency' else '' }}" href="/emergency">🚨 Emergency Help</a>
  <a class="nav-link {{ 'active' if active_page == 'history' else '' }}" href="/history">📜 History</a>
  <a class="nav-link" href="/new">✨ New Session</a>

  {% if sessions %}
  <div class="section-title">Recent Sessions</div>
  {% for s in sessions[:8] %}
    <a href="/?session={{ s.id }}" class="session-item">
      <span>{{ s.title[:18] }}{% if s.title|length > 18 %}...{% endif %}</span>
      <span class="badge">{{ s.count }}</span>
    </a>
  {% endfor %}
  {% endif %}

  <div class="disclaimer">
    ⚠️ General legal information only. Please consult a qualified advocate for case-specific advice.
  </div>
</aside>

<main class="main">
  {% block content %}{% endblock %}
</main>
</body>
</html>
"""

CHAT_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>💬 Multilingual Legal Chat</h1>
    <p>Ask in English, हिंदी, or मराठी.</p>
  </div>
  <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
    <select id="language-select" style="width:auto;">
      <option value="en" {% if current_language=='en' %}selected{% endif %}>English</option>
      <option value="hi" {% if current_language=='hi' %}selected{% endif %}>हिंदी</option>
      <option value="mr" {% if current_language=='mr' %}selected{% endif %}>मराठी</option>
    </select>
    <a href="/new" class="btn btn-outline">✨ New Chat</a>
  </div>
</div>

<div class="content" style="display:flex; flex-direction:column;">
  <div id="messages" style="flex:1; overflow:auto; padding:22px; display:flex; flex-direction:column; gap:14px;">
    {% if not messages %}
      <div class="card">
        <h3 style="margin-bottom:10px;">Try these</h3>
        <div class="grid grid-2">
          <button class="btn btn-outline" onclick="setInput('What is IPC Section 420?')">IPC 420</button>
          <button class="btn btn-outline" onclick="setInput('FIR कैसे दर्ज करें?')">FIR कैसे दर्ज करें?</button>
          <button class="btn btn-outline" onclick="setInput('माझा चेक बाऊन्स झाला, आता काय करू?')">Cheque Bounce</button>
          <button class="btn btn-outline" onclick="setInput('What are my rights in domestic violence case?')">Domestic Violence</button>
        </div>
      </div>
    {% endif %}

    {% for msg in messages %}
    <div style="display:flex; justify-content:{{ 'flex-end' if msg.role=='user' else 'flex-start' }};">
      <div style="max-width:820px; background:{{ '#6c63ff' if msg.role=='user' else '#212836' }}; padding:14px 16px; border-radius:14px; border:1px solid #30384a; line-height:1.7; white-space:pre-wrap;">
        {{ msg.content }}
      </div>
    </div>
    {% endfor %}
  </div>

  <div style="padding:18px 22px; border-top:1px solid var(--border); background:var(--surface);">
    <div class="grid" style="grid-template-columns:1fr auto auto auto;">
      <textarea id="user-input" placeholder="Type your legal question..."></textarea>
      <button class="btn btn-outline" type="button" onclick="startVoice()">🎤 Voice</button>
      <button class="btn btn-outline" type="button" onclick="downloadLastReply()">⬇ Reply</button>
      <button class="btn btn-primary" type="button" onclick="sendMessage()">Send</button>
    </div>
    <div class="small" style="margin-top:8px;">Tip: press Enter to send, Shift+Enter for new line.</div>
  </div>
</div>

<input type="hidden" id="session-id" value="{{ session_id }}">

<script>
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const langEl = document.getElementById('language-select');

function setInput(text){ inputEl.value = text; inputEl.focus(); }

function appendMessage(role, content){
  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.justifyContent = role === 'user' ? 'flex-end' : 'flex-start';

  const bubble = document.createElement('div');
  bubble.className = "chat-bubble";
  bubble.dataset.role = role;
  bubble.style.maxWidth = '820px';
  bubble.style.padding = '14px 16px';
  bubble.style.borderRadius = '14px';
  bubble.style.border = '1px solid #30384a';
  bubble.style.lineHeight = '1.7';
  bubble.style.whiteSpace = 'pre-wrap';
  bubble.style.wordBreak = 'break-word';
  bubble.style.background = role === 'user' ? '#6c63ff' : '#212836';
  bubble.textContent = content;

  row.appendChild(bubble);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

inputEl.addEventListener('keydown', async (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    await sendMessage();
  }
});

langEl.addEventListener('change', async () => {
  await fetch('/api/set-language', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ language: langEl.value, session_id: document.getElementById('session-id').value })
  });
});

async function sendMessage(){
  const message = inputEl.value.trim();
  if(!message) return;

  const sessionId = document.getElementById('session-id').value;
  appendMessage('user', message);
  inputEl.value = '';

  const res = await fetch('/api/chat', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      message: message,
      session_id: sessionId,
      language: langEl.value
    })
  });

  const data = await res.json();
  appendMessage('assistant', data.reply || data.error || 'Something went wrong');
}

function startVoice(){
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert('Speech recognition is not supported in this browser.');
    return;
  }

  const recognition = new SpeechRecognition();
  const selected = langEl.value;
  recognition.lang = selected === 'hi' ? 'hi-IN' : (selected === 'mr' ? 'mr-IN' : 'en-IN');
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = function(event){
    inputEl.value = event.results[0][0].transcript;
  };

  recognition.onerror = function(){
    alert('Voice input failed. Please try again.');
  };

  recognition.start();
}

function downloadLastReply(){
  const bubbles = [...document.querySelectorAll('.chat-bubble')].filter(x => x.dataset.role === 'assistant');
  if(!bubbles.length){
    alert('No assistant reply found yet.');
    return;
  }
  const text = bubbles[bubbles.length - 1].textContent;
  const blob = new Blob([text], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'legal_reply.txt';
  a.click();
}
</script>
"""
)

DOCS_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>📄 Document Analysis</h1>
    <p>Upload PDF or TXT and get a legal summary.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  <div class="card">
    <form id="doc-form">
      <div class="grid grid-2">
        <div>
          <label class="small">Language</label>
          <select id="doc-language" name="language">
            <option value="en">English</option>
            <option value="hi">हिंदी</option>
            <option value="mr">मराठी</option>
          </select>
        </div>
        <div>
          <label class="small">Choose file</label>
          <input type="file" id="file-input" name="file" accept=".pdf,.txt">
        </div>
      </div>
      <div style="margin-top:16px;">
        <button class="btn btn-primary" type="submit">Analyse Document</button>
      </div>
    </form>
  </div>

  <div class="card" style="margin-top:20px;">
    <h3 style="margin-bottom:10px;">Result</h3>
    <div id="doc-result" class="result-box">Upload a file to analyse.</div>
  </div>

  {% if recent_docs %}
  <div class="card" style="margin-top:20px;">
    <h3 style="margin-bottom:10px;">Recent Analyses</h3>
    {% for doc in recent_docs %}
      <div style="padding:12px 0; border-bottom:1px solid var(--border);">
        <strong>{{ doc.filename }}</strong>
        <div class="small">{{ doc.uploaded }}</div>
      </div>
    {% endfor %}
  </div>
  {% endif %}
</div>

<script>
document.getElementById('doc-form').addEventListener('submit', async function(e){
  e.preventDefault();
  const fd = new FormData();
  const file = document.getElementById('file-input').files[0];
  const lang = document.getElementById('doc-language').value;
  if(!file){ alert('Select a file first'); return; }
  fd.append('file', file);
  fd.append('language', lang);

  document.getElementById('doc-result').textContent = 'Analysing...';

  const res = await fetch('/api/analyse', { method:'POST', body:fd });
  const data = await res.json();
  document.getElementById('doc-result').textContent = data.analysis || data.error || 'Error';
});
</script>
"""
)

FIR_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>📝 FIR Guidance Wizard</h1>
    <p>Get step-by-step guidance for filing an FIR.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  <div class="card">
    <form id="fir-form" class="grid grid-2">
      <div>
        <label class="small">Language</label>
        <select name="language">
          <option value="en">English</option>
          <option value="hi">हिंदी</option>
          <option value="mr">मराठी</option>
        </select>
      </div>
      <div>
        <label class="small">Incident Type</label>
        <input name="incident_type" placeholder="Theft, assault, cheating, harassment...">
      </div>
      <div>
        <label class="small">Incident Date</label>
        <input name="incident_date" placeholder="DD/MM/YYYY">
      </div>
      <div>
        <label class="small">Incident Place</label>
        <input name="incident_place" placeholder="City / area / police station jurisdiction">
      </div>
      <div>
        <label class="small">Is accused known?</label>
        <select name="accused_known">
          <option>Yes</option>
          <option>No</option>
          <option>Partially</option>
        </select>
      </div>
      <div>
        <label class="small">Police complaint status</label>
        <select name="police_status">
          <option>Not filed yet</option>
          <option>Approached police informally</option>
          <option>Written complaint given</option>
          <option>FIR refused</option>
        </select>
      </div>
      <div style="grid-column:1/-1;">
        <label class="small">Available Evidence</label>
        <textarea name="evidence" placeholder="Screenshots, medical report, witnesses, recordings, bills, bank records..."></textarea>
      </div>
      <div style="grid-column:1/-1;">
        <button class="btn btn-primary" type="submit">Generate FIR Guidance</button>
      </div>
    </form>
  </div>

  <div class="card" style="margin-top:20px;">
    <h3 style="margin-bottom:10px;">Guidance</h3>
    <div id="fir-result" class="result-box">Fill the form and submit.</div>
    <div class="action-row">
      <button class="btn btn-outline" type="button" onclick="downloadFirPdf()">⬇ Download PDF</button>
      <button class="btn btn-outline" type="button" onclick="downloadFirText()">⬇ Download Text</button>
    </div>
  </div>
</div>

<script>
function downloadFirText(){
  const text = document.getElementById('fir-result').textContent.trim();
  if(!text || text === 'Fill the form and submit.'){ alert('Generate FIR guidance first.'); return; }
  const blob = new Blob([text], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'fir_guidance.txt';
  a.click();
}

async function downloadFirPdf(){
  const text = document.getElementById('fir-result').textContent.trim();
  if(!text || text === 'Fill the form and submit.'){ alert('Generate FIR guidance first.'); return; }

  const res = await fetch('/api/download-pdf', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      title: 'FIR Guidance',
      content: text,
      filename: 'fir_guidance.pdf'
    })
  });

  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'fir_guidance.pdf';
  a.click();
}

document.getElementById('fir-form').addEventListener('submit', async function(e){
  e.preventDefault();
  const formData = Object.fromEntries(new FormData(this).entries());
  document.getElementById('fir-result').textContent = 'Generating guidance...';

  const res = await fetch('/api/fir-guidance', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(formData)
  });

  const data = await res.json();
  document.getElementById('fir-result').textContent = data.result || data.error || 'Error';
});
</script>
"""
)

DRAFT_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>📨 Complaint / Notice Generator</h1>
    <p>Create a first draft for complaint, notice, or application.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  <div class="card">
    <form id="draft-form" class="grid grid-2">
      <div>
        <label class="small">Language</label>
        <select name="language">
          <option value="en">English</option>
          <option value="hi">हिंदी</option>
          <option value="mr">मराठी</option>
        </select>
      </div>
      <div>
        <label class="small">Draft Type</label>
        <select name="draft_type">
          <option>Police Complaint</option>
          <option>Legal Notice</option>
          <option>Consumer Complaint</option>
          <option>RTI Application</option>
        </select>
      </div>
      <div>
        <label class="small">Your Name</label>
        <input name="name" placeholder="Enter your name">
      </div>
      <div>
        <label class="small">Against / To</label>
        <input name="opponent" placeholder="Person, company, authority">
      </div>
      <div style="grid-column:1/-1;">
        <label class="small">Facts</label>
        <textarea name="facts" placeholder="Describe the incident clearly"></textarea>
      </div>
      <div style="grid-column:1/-1;">
        <label class="small">Relief / What you want</label>
        <textarea name="relief" placeholder="What action do you want?"></textarea>
      </div>
      <div style="grid-column:1/-1;">
        <button class="btn btn-primary" type="submit">Generate Draft</button>
      </div>
    </form>
  </div>

  <div class="card" style="margin-top:20px;">
    <h3 style="margin-bottom:10px;">Draft Result</h3>
    <div id="draft-result" class="result-box">Fill the form and submit.</div>
    <div class="action-row">
      <button class="btn btn-outline" type="button" onclick="downloadDraftText()">⬇ Download Text</button>
      <button class="btn btn-outline" type="button" onclick="downloadDraftPdf()">⬇ Download PDF</button>
    </div>
  </div>
</div>

<script>
function downloadDraftText(){
  const text = document.getElementById('draft-result').textContent.trim();
  if(!text || text === 'Fill the form and submit.'){ alert('Generate draft first.'); return; }
  const blob = new Blob([text], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'legal_draft.txt';
  a.click();
}

async function downloadDraftPdf(){
  const text = document.getElementById('draft-result').textContent.trim();
  if(!text || text === 'Fill the form and submit.'){ alert('Generate draft first.'); return; }

  const res = await fetch('/api/download-pdf', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      title: 'Legal Draft',
      content: text,
      filename: 'legal_draft.pdf'
    })
  });

  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'legal_draft.pdf';
  a.click();
}

document.getElementById('draft-form').addEventListener('submit', async function(e){
  e.preventDefault();
  const formData = Object.fromEntries(new FormData(this).entries());
  document.getElementById('draft-result').textContent = 'Generating draft...';

  const res = await fetch('/api/generate-draft', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(formData)
  });

  const data = await res.json();
  document.getElementById('draft-result').textContent = data.result || data.error || 'Error';
});
</script>
"""
)

LOOKUP_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>📚 IPC / BNS Lookup</h1>
    <p>Search by section number like 302, 420, 498A, 138.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  <div class="card">
    <form method="GET" action="/ipc-bns-lookup" class="grid" style="grid-template-columns:1fr auto;">
      <input type="text" name="q" placeholder="Enter section number" value="{{ query or '' }}">
      <button class="btn btn-primary" type="submit">Search</button>
    </form>
  </div>

  <div class="card" style="margin-top:20px;">
    {% if result %}
      <h3 style="margin-bottom:14px;">Section Details</h3>
      <div class="result-box">
Old Law: {{ result.old_law }}

Current / Related Law: {{ result.new_law }}

Topic: {{ result.topic }}

Punishment: {{ result.punishment }}

Bailable: {{ result.bailable }}

Cognizable: {{ result.cognizable }}
      </div>
    {% else %}
      <div class="result-box">No result yet. Try 302, 420, 498A, or 138.</div>
    {% endif %}
  </div>
</div>
"""
)

EMERGENCY_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>🚨 Emergency Help</h1>
    <p>Important helplines you may need immediately.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  <div class="grid grid-2">
    {% for item in contacts %}
    <div class="card">
      <h3>{{ item.name }}</h3>
      <p style="margin-top:8px; font-size:24px; color:var(--accent2);">{{ item.number }}</p>
    </div>
    {% endfor %}
  </div>

  <div class="card" style="margin-top:20px;">
    <h3 style="margin-bottom:10px;">Important note</h3>
    <p style="line-height:1.8; color:var(--muted);">
      If there is immediate danger, threat, violence, serious harassment, child abuse, or urgent medical need,
      contact emergency authorities right away and seek local legal help.
    </p>
  </div>
</div>
"""
)

HISTORY_TEMPLATE = BASE_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
<div class="header">
  <div>
    <h1>📜 Chat History</h1>
    <p>Your recent legal sessions.</p>
  </div>
</div>

<div class="content" style="padding:24px;">
  {% if sessions %}
    {% for s in sessions %}
    <div class="card" style="margin-bottom:16px;">
      <div style="display:flex; justify-content:space-between; gap:10px; align-items:center; flex-wrap:wrap;">
        <div>
          <h3>{{ s.title }}</h3>
          <div class="small">{{ s.created }} · {{ s.count }} messages · {{ s.language }}</div>
        </div>
        <a href="/?session={{ s.id }}" class="btn btn-outline">Open</a>
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="card"><p>No history yet.</p></div>
  {% endif %}
</div>
"""
)

# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────
@app.route("/")
def chat_page():
    requested_sid = request.args.get("session")
    if requested_sid:
        session["sid"] = requested_sid

    sid = get_session_id()
    sessions = get_all_sessions()
    messages = get_session_messages(sid)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT language FROM sessions WHERE session_id=?", (sid,))
    row = cur.fetchone()
    conn.close()
    current_language = row[0] if row and row[0] else "en"

    return render_template_string(
        CHAT_TEMPLATE,
        messages=messages,
        session_id=sid,
        sessions=sessions,
        current_language=current_language,
        active_page="chat"
    )

@app.route("/new")
def new_session():
    session["sid"] = str(uuid.uuid4())
    return redirect(url_for("chat_page"))

@app.route("/documents")
def documents_page():
    return render_template_string(
        DOCS_TEMPLATE,
        recent_docs=get_recent_documents(),
        sessions=get_all_sessions(),
        active_page="docs"
    )

@app.route("/fir-wizard")
def fir_wizard_page():
    return render_template_string(
        FIR_TEMPLATE,
        sessions=get_all_sessions(),
        active_page="fir"
    )

@app.route("/draft-generator")
def draft_generator_page():
    return render_template_string(
        DRAFT_TEMPLATE,
        sessions=get_all_sessions(),
        active_page="draft"
    )

@app.route("/ipc-bns-lookup")
def lookup_page():
    query = (request.args.get("q") or "").strip().upper()
    result = IPC_BNS_DATA.get(query)
    return render_template_string(
        LOOKUP_TEMPLATE,
        sessions=get_all_sessions(),
        active_page="lookup",
        query=query,
        result=result
    )

@app.route("/emergency")
def emergency_page():
    return render_template_string(
        EMERGENCY_TEMPLATE,
        sessions=get_all_sessions(),
        active_page="emergency",
        contacts=EMERGENCY_CONTACTS
    )

@app.route("/history")
def history_page():
    return render_template_string(
        HISTORY_TEMPLATE,
        sessions=get_all_sessions(),
        active_page="history"
    )

# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@app.route("/api/set-language", methods=["POST"])
def api_set_language():
    data = request.get_json(force=True)
    sid = data.get("session_id") or get_session_id()
    language = data.get("language", "en")
    if language not in LANGUAGE_MAP:
        language = "en"
    update_session_language(sid, language)
    return jsonify({"ok": True})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    message = (data or {}).get("message", "").strip()
    sid = (data or {}).get("session_id") or get_session_id()
    language = (data or {}).get("language", "en")

    if language not in LANGUAGE_MAP:
        language = "en"

    if not message:
        return jsonify({"error": "Empty message"}), 400

    update_session_language(sid, language)
    history = get_session_messages(sid)

    save_message(sid, "user", message)
    if len(history) == 0:
        update_session_title(sid, guess_title_from_message(message))

    reply = safe_model_reply(message, history, language)
    save_message(sid, "assistant", reply)

    return jsonify({"reply": reply, "session_id": sid})

@app.route("/api/analyse", methods=["POST"])
def api_analyse():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    language = request.form.get("language", "en")

    if f.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(f.filename):
        return jsonify({"error": "Only PDF and TXT files are supported"}), 400

    filename = secure_filename(f.filename)
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    f.save(save_path)

    text = extract_text(save_path, filename)
    if not text or len(text.strip()) < 50:
        return jsonify({"error": "Could not extract meaningful text from file"}), 400

    analysis = analyze_document(text, filename, language)
    save_document_analysis(filename, analysis)

    return jsonify({"analysis": analysis, "filename": filename})

@app.route("/api/fir-guidance", methods=["POST"])
def api_fir_guidance():
    data = request.get_json(force=True)
    language = data.get("language", "en")
    result = generate_fir_guidance(data, language)
    return jsonify({"result": result})

@app.route("/api/generate-draft", methods=["POST"])
def api_generate_draft():
    data = request.get_json(force=True)
    language = data.get("language", "en")
    result = generate_legal_draft(data, language)
    return jsonify({"result": result})

@app.route("/api/download-pdf", methods=["POST"])
def api_download_pdf():
    data = request.get_json(force=True)
    title = (data.get("title") or "Document").strip()
    content = (data.get("content") or "").strip()
    filename = (data.get("filename") or "document.pdf").strip()

    if not content:
        return jsonify({"error": "No content to export"}), 400

    pdf_buffer = make_text_pdf(title, content)
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )

# ─────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────────────────────
@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": f"File too large. Max size is {MAX_CONTENT_MB} MB."}), 413

@app.errorhandler(404)
def not_found(e):
    return redirect(url_for("chat_page"))

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("AI Legal Advisor — Indian Law")
    print("=" * 60)
    print(f"Open: http://127.0.0.1:{PORT}")
    print("Routes:")
    print("/               -> Chat")
    print("/documents       -> Document analysis")
    print("/fir-wizard      -> FIR guidance")
    print("/draft-generator -> Draft generator")
    print("/ipc-bns-lookup  -> IPC/BNS lookup")
    print("/emergency       -> Emergency help")
    print("/history         -> Chat history")
    print("=" * 60)

    if not GROQ_API_KEY:
        print("WARNING: GROQ_API_KEY not set.")
        print('PowerShell:')
        print('$env:GROQ_API_KEY="your_new_key_here"')
        print("python app.py")
        print("=" * 60)

    app.run(host="127.0.0.1", port=PORT, debug=True)