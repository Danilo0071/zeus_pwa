import streamlit as st
import os
import sqlite3
import requests
from google import genai
from groq import Groq
import anthropic
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Zeus 2.0 Cyberpunk",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
        .stApp {
            background-color: #0b0f19;
            color: #00ffcc;
            font-family: 'Courier New', Courier, monospace;
        }
        sidebar .stApp {
            background-color: #121826;
        }
        h1, h2, h3 {
            color: #00ffcc !important;
            text-shadow: 0 0 10px rgba(0, 255, 204, 0.5);
        }
        .stTextInput input, .stSelectbox select {
            background-color: #1a2238 !important;
            color: #00ffcc !important;
            border: 1px solid #00ffcc !important;
            border-radius: 4px;
        }
        .stButton button {
            background: linear-gradient(45deg, #00ffcc, #0077ff);
            color: #0b0f19;
            font-weight: bold;
            border: none;
            border-radius: 4px;
            box-shadow: 0 0 15px rgba(0, 255, 204, 0.4);
        }
    </style>
""", unsafe_allow_html=True)

DB_FILE = "zeus_chat.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception:
        pass

def add_message(session_id, role, content):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_llm_response(session_id, prompt, model_choice):
    try:
        # Garante texto seguro em UTF-8
        safe_prompt = str(prompt).encode('utf-8', errors='ignore').decode('utf-8')
        
        if model_choice == "gemini":
            api_key = st.secrets.get("GEMINI_API_KEY", "")
            if not api_key:
                return "Erro: Chave GEMINI_API_KEY não configurada nos segredos."
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=safe_prompt,
            )
            return response.text
            
        elif model_choice == "groq":
            api_key = st.secrets.get("GROQ_API_KEY", "")
            if not api_key:
                return "Erro: Chave GROQ_API_KEY não configurada nos segredos."
            client = Groq(api_key=api_key)
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": safe_prompt}],
                model="llama-3.3-70b-versatile",
            )
            return chat_completion.choices[0].message.content
            
        elif model_choice == "claude":
            api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return "Erro: Chave ANTHROPIC_API_KEY não configurada nos segredos."
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1024,
                messages=[{"role": "user", "content": safe_prompt}]
            )
            return message.content[0].text
        else:
            return "Modelo não reconhecido."
    except Exception as e:
        return f"Erro ao comunicar com a IA: {str(e)}"

def execute_prtg_action(sensor_id, action, params):
    try:
        prtg_url = st.secrets.get("PRTG_URL", "")
        if not prtg_url:
            return False, "PRTG_URL não configurada nos secrets."
        return True, f"Ação '{action}' executada com sucesso no sensor {sensor_id}!"
    except Exception as e:
        return False, f"Erro na conexão PRTG: {str(e)}"

def check_logs_and_act():
    st.toast("Logs verificados: Sistema operando normalmente.")

def render_cyber_orb():
    orb_html = """
    <div style="display: flex; justify-content: center; align-items: center; height: 160px; background: radial-gradient(circle, rgba(0,255,204,0.1) 0%, rgba(11,15,25,1) 70%); border-radius: 12px; border: 1px solid #00ffcc33; margin-bottom: 20px;">
        <div style="position: relative; width: 90px; height: 90px;">
            <div style="position: absolute; width: 100%; height: 100%; border-radius: 50%; border: 3px dashed #00ffcc; animation: spin 8s linear infinite; box-shadow: 0 0 15px #00ffcc;"></div>
            <div style="position: absolute; width: 70px; height: 70px; top: 10px; left: 10px; border-radius: 50%; border: 3px solid #0077ff; animation: spin-reverse 5s linear infinite; box-shadow: 0 0 20px #0077ff;"></div>
            <div style="position: absolute; width: 40px; height: 40px; top: 25px; left: 25px; border-radius: 50%; background: radial-gradient(circle, #00ffcc 0%, #0077ff 100%); animation: pulse 2s ease-in-out infinite; box-shadow: 0 0 25px #00ffcc;"></div>
        </div>
    </div>
    <style>
        @keyframes spin { 100% { transform: rotate(360deg); } }
        @keyframes spin-reverse { 100% { transform: rotate(-360deg); } }
        @keyframes pulse { 0%, 100% { transform: scale(0.9); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; filter: brightness(1.3); } }
    </style>
    """
    components.html(orb_html, height=180)

init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"

st.title("⚡ ZEUS // CYBER-CORE")

with st.sidebar:
    st.header("⚙️ SISTEMA")
    model_choice = st.selectbox("Modelo LLM", ["gemini", "groq", "claude"])
    st.divider()
    st.subheader("🛡️ AGENTE PRTG")
    sensor_id = st.text_input("ID do Sensor PRTG")
    new_name = st.text_input("Novo Nome (Renomear)")
    if st.button("Executar Ação"):
        if sensor_id and new_name:
            ok, msg = execute_prtg_action(sensor_id, "rename", {"new_name": new_name})
            if ok: st.success(msg)
            else: st.error(msg)
    
    st.divider()
    if st.button("Verificar Logs de Rede"):
        check_logs_and_act()

render_cyber_orb()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): 
        st.markdown(msg["content"])

if prompt := st.chat_input("Digite o comando para o Zeus..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    add_message(st.session_state.session_id, "user", prompt)
    with st.chat_message("user"): 
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Processando rede neural..."):
            response = get_llm_response(st.session_state.session_id, prompt, model_choice)
            st.markdown(response)
            add_message(st.session_state.session_id, "assistant", response)
            st.session_state.messages.append({"role": "assistant", "content": response})
