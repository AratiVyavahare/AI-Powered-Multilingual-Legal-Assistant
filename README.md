# ⚖️ AI-Powered Multilingual Legal Assistant

AI-Powered Multilingual Legal Assistant is a Flask-based web application designed to provide instant legal guidance based on Indian law. The application uses the Groq API (OpenAI-compatible) to deliver AI-powered legal assistance in **English, Hindi, and Marathi**.

The system helps users with:
- Legal chat assistance
- FIR guidance
- Complaint and legal notice generation
- IPC/BNS section lookup
- Legal document analysis
- Emergency legal help information

---

# ✨ Features

- 🌐 Multilingual chatbot (English, Hindi, Marathi)
- ⚖️ AI-powered legal guidance
- 📋 FIR Guidance Wizard
- 📝 Complaint, Legal Notice, Consumer Complaint & RTI Draft Generator
- 📖 IPC/BNS Section Lookup
- 📄 PDF and TXT document analysis
- 💬 Chat history with session management
- 🎤 Voice input using browser speech recognition
- 🚨 Emergency legal helpline information
- 📥 Download legal drafts and guidance as PDF
- 📄 Download chat responses as text

---

# 🛠 Tech Stack

## Frontend
- HTML
- CSS
- JavaScript
- Jinja2 Templates

## Backend
- Python
- Flask
- SQLite

## Libraries
- OpenAI Python SDK (Groq OpenAI-compatible API)
- pdfplumber
- reportlab
- Werkzeug

---

# 📂 Project Structure

```text
AI-Powered-Multilingual-Legal-Assistant/
│── app.py
│── requirements.txt
│── README.md
│── uploads/
│── ipc_sections.csv
│── legal_advisor.db
```

---

# 🚀 Installation

## 1. Clone the Repository

```bash
git clone https://github.com/AratiVyavahare/AI-Powered-Multilingual-Legal-Assistant.git
cd AI-Powered-Multilingual-Legal-Assistant
```

## 2. Create a Virtual Environment

### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

### Linux/macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## 4. Set the Groq API Key

### Windows PowerShell

```powershell
$env:GROQ_API_KEY="your_groq_api_key"
```

### Linux/macOS

```bash
export GROQ_API_KEY="your_groq_api_key"
```

## 5. Run the Application

```bash
python app.py
```

Open your browser and visit:

```
http://127.0.0.1:5000
```

---

# 📌 Main Routes

| Route | Description |
|--------|-------------|
| `/` | Multilingual Legal Chatbot |
| `/documents` | Legal Document Analysis |
| `/fir-wizard` | FIR Guidance Wizard |
| `/draft-generator` | Draft Generator |
| `/ipc-bns-lookup` | IPC/BNS Lookup |
| `/emergency` | Emergency Legal Help |
| `/history` | Chat History |
| `/new` | Start New Session |

---

# 📄 Supported File Types

- PDF (.pdf)
- Text (.txt)

**Maximum Upload Size:** 16 MB

---

# 🔑 Key Functional Modules

## 1. Legal Chatbot
Provides AI-generated legal guidance in English, Hindi, and Marathi with:
- Relevant legal sections
- Simple explanations
- Recommended next steps
- Required documents
- Legal disclaimer

## 2. FIR Guidance Wizard
Helps users understand the FIR filing process by collecting:
- Incident type
- Date and location
- Suspect information
- Available evidence
- Police complaint status

## 3. Draft Generator
Generates first drafts for:
- Police Complaint
- Legal Notice
- Consumer Complaint
- RTI Application

## 4. IPC/BNS Lookup
Allows users to search IPC/BNS sections such as:
- 302
- 420
- 498A
- 138

## 5. Document Analysis
Uploads legal documents and provides:
- Document type
- Summary
- Key clauses
- Relevant laws
- Risk analysis
- Suggested next steps

---

# 🗄 Database

The application uses SQLite.

Database file:

```
legal_advisor.db
```

Tables:
- sessions
- messages
- documents

---

# 📦 Dependencies

- Flask
- OpenAI
- pdfplumber
- reportlab
- Werkzeug

---

# 🔮 Future Improvements

- User authentication
- Advocate consultation integration
- Case tracking dashboard
- DOCX document support
- Enhanced BNS/BNSS mapping
- Cloud deployment
- Improved legal knowledge base

---

# ⚠️ Disclaimer

This project is developed for educational and assistance purposes only. It does not replace professional legal advice. Users should consult a qualified legal professional before taking any legal action.

---

# 👩‍💻 Author

**Arati Vyavahare**

GitHub: https://github.com/AratiVyavahare

---

# 📄 License

This project is intended for educational and learning purposes. You may add an open-source license such as the MIT License if you wish to make the project publicly reusable.
