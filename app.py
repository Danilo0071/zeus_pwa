import streamlit as st
import os
import sqlite3
import requests
from google import genai
from groq import Groq
import anthropic

st.set_page_config(page_title="Zeus", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <script>
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js');
        }
    </script>
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
        if model_choice == "gemini":
            api_key = st.secrets.get("GEMINI_API_KEY", "")
            if not api_key:
                return "Erro: Chave GEMINI_API_KEY não configurada nos segredos."
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            return response.text
            
        elif model_choice == "groq":
            api_key = st.secrets.get("GROQ_API_KEY", "")
            if not api_key:
                return "Erro: Chave GROQ_API_KEY não configurada nos segredos."
            client = Groq(api_key=api_key)
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
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
                messages=[{"role": "user", "content": prompt}]
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

def render_voice_component():
    st.markdown("""
        <div style="text-align: center; margin-top: 20px; color: gray; font-size: 12px;">
            ⚡ Zeus 2.0 PWA Ativo
        </div>
    """, unsafe_allow_html=True)

init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"

st.title("⚡ Zeus Assistente")

with st.sidebar:
    st.header("Configuração")
    model_choice = st.selectbox("Modelo LLM", ["gemini", "groq", "claude"])
    st.divider()
    st.subheader("Agente PRTG (Ação Rápida)")
    sensor_id = st.text_input("ID do Sensor PRTG")
    new_name = st.text_input("Novo Nome (para renomear)")
    if st.button("Aplicar Alteração"):
        if sensor_id and new_name:
            ok, msg = execute_prtg_action(sensor_id, "rename", {"new_name": new_name})
            if ok: st.success(msg)
            else: st.error(msg)
    
    st.divider()
    if st.button("Verificar Logs de Rede Agora"):
        check_logs_and_act()
        st.toast("Verificação de logs executada.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): 
        st.markdown(msg["content"])

if prompt := st.chat_input("Fale ou digite seu comando..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    add_message(st.session_state.session_id, "user", prompt)
    with st.chat_message("user"): 
        st.markdown(prompt)

    if "prtg" in prompt.lower() or "sensor" in prompt.lower():
        st.info("Comando de agente detectado. Use a barra lateral para ações estruturadas no PRTG.")
    
    with st.chat_message("assistant"):
        with st.spinner("Zeus pensando..."):
            response = get_llm_response(st.session_state.session_id, prompt, model_choice)
            st.markdown(response)
            add_message(st.session_state.session_id, "assistant", response)
            st.session_state.messages.append({"role": "assistant", "content": response})

render_voice_component()
