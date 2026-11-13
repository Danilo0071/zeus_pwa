import os
import json
import sqlite3
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_mic_recorder import mic_recorder
import base64

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(page_title="Zeus 2.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# TEMAS
# ============================================================
THEMES = {
    "Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"},
    "Matrix": {"bg": "#000000", "panel": "#031a06", "primary": "#00ff41", "secondary": "#008f11", "text": "#00ff41", "border": "#00ff4133"},
    "Neon Purple": {"bg": "#0e0118", "panel": "#1a0533", "primary": "#ff00ff", "secondary": "#a020f0", "text": "#ff99ff", "border": "#ff00ff33"},
    "Minimal Light": {"bg": "#f5f5f5", "panel": "#ffffff", "primary": "#0066ff", "secondary": "#00aaff", "text": "#111111", "border": "#0066ff33"},
}

def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(f"""
        <style>
            .stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {t['panel']}; }}
            h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}
            .stTextInput input, .stSelectbox select, .stTextArea textarea {{ background-color: {t['panel']} !important; color: {t['text']} !important; border: 1px solid {t['primary']} !important; border-radius: 4px; }}
            .stButton button {{ background: linear-gradient(45deg, {t['primary']}, {t['secondary']}); color: {t['bg']}; font-weight: bold; border: none; border-radius: 4px; box-shadow: 0 0 15px {t['border']}; }}
            .stChatMessage {{ background: {t['panel']} !important; border: 1px solid {t['border']}; }}
        </style>
    """, unsafe_allow_html=True)

st.markdown("""<link rel="manifest" href="/manifest.json"><meta name="theme-color" content="#0b0f19"><script>if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }</script>""", unsafe_allow_html=True)

# ============================================================
# BANCO DE DADOS
# ============================================================
DB_FILE = "zeus_chat.db"
def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.commit(); conn.close()
    except Exception: pass

def add_message(session_id, role, content):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit(); conn.close()
    except Exception: pass

def get_recent_context(session_id, limit=6):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception: return []

# ============================================================
# CHAVES
# ============================================================
def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val: return str(val).strip()
    except Exception: pass
    return os.environ.get(key, default).strip()

# ============================================================
# TRANSCRIÇÃO DE ÁUDIO (STT)
# ============================================================
def transcrever_audio(audio_bytes):
    api_key = _secret("GEMINI_API_KEY")
    if not api_key: return "⚠️ GEMINI_API_KEY ausente."
    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    payload = {"contents": [{"parts": [{"text": "Transcreva este áudio para texto em português. Retorne apenas a transcrição."}, {"inline_data": {"mime_type": "audio/webm", "data": audio_b64}}]}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code == 200: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return f"❌ Erro na transcrição ({r.status_code}): {r.text[:200]}"
    except Exception as e: return f"Erro ao transcrever: {e}"

# ============================================================
# CLIENTE LLM (GROQ, CLAUDE, GEMINI)
# ============================================================
def get_llm_response(session_id, prompt, model_choice):
    history = get_recent_context(session_id)
    msgs = history + [{"role": "user", "content": prompt}]
    sys_prompt = "Você é Zeus, assistente sênior de DevOps e redes. Responda em português, de forma objetiva e técnica."

    try:
        if model_choice == "groq":
            api_key = _secret("GROQ_API_KEY")
            if not api_key: return "⚠️ GROQ_API_KEY ausente."
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sys_prompt}] + msgs, "temperature": 0.4}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200: return r.json()["choices"][0]["message"]["content"]
            return f"❌ Erro Groq ({r.status_code}): {r.text[:200]}"

        elif model_choice == "claude":
            api_key = _secret("ANTHROPIC_API_KEY")
            if not api_key: return "⚠️ ANTHROPIC_API_KEY ausente."
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            payload = {"model": "claude-3-5-haiku-20241022", "max_tokens": 1024, "system": sys_prompt, "messages": [m for m in msgs if m["role"] != "system"]}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200: return r.json()["content"][0]["text"]
            return f"❌ Erro Claude ({r.status_code}): {r.text[:200]}"

        elif model_choice == "gemini":
            api_key = _secret("GEMINI_API_KEY")
            if not api_key: return "⚠️ GEMINI_API_KEY ausente."
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
            contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
            payload = {"systemInstruction": {"parts": [{"text": sys_prompt}]}, "contents": contents}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return f"❌ Erro Gemini ({r.status_code}): {r.text[:200]}"

        return "Modelo não reconhecido."
    except Exception as e: return f"Erro de conexão: {e}"

# ============================================================
# AVATARES (USANDO AS IMAGENS DO GITHUB)
# ============================================================
def render_avatar(avatar_type="Olho Cibernético", theme_name="Cyberpunk"):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    
    # Links diretos para as imagens no seu GitHub
    github_url = "https://raw.githubusercontent.com/Danilo0071/zeus_pwa/main/"
    img_olho = f"{github_url}olho.jpg"
    img_robo = f"{github_url}robo.jpg"
    img_esfera = f"{github_url}esfera.jpg"

    css_base = f"""
    <style>
        body {{ margin: 0; background: transparent; overflow: hidden; display: flex; justify-content: center; align-items: center; height: 100vh; }}
        .avatar-container {{ 
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            padding: 20px; background: radial-gradient(circle, {t['panel']} 0%, {t['bg']} 80%);
            border: 1px solid {t['border']}; border-radius: 12px; margin-bottom: 15px;
            box-shadow: 0 0 20px {t['border']}; height: 260px; position: relative;
        }}
        @keyframes pulse {{ 0%,100% {{ transform: scale(0.95); opacity: .8; }} 50% {{ transform: scale(1.05); opacity: 1; }} }}
        @keyframes float {{ 0%,100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-15px); }} }}
        @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
        @keyframes blink-lid {{ 0%, 95%, 100% {{ transform: scaleY(0); }} 98% {{ transform: scaleY(1); }} }}
        @keyframes move-iris {{ 0% {{ transform: translate(0, 0) scale(1); }} 25% {{ transform: translate(15px, -5px) scale(1.05); }} 50% {{ transform: translate(-15px, 5px) scale(1); }} 75% {{ transform: translate(10px, 10px) scale(0.95); }} 100% {{ transform: translate(0, 0) scale(1); }} }}
        @keyframes glow-pulse {{ 0%, 100% {{ box-shadow: 0 0 20px {t['primary']}; }} 50% {{ box-shadow: 0 0 50px {t['primary']}, 0 0 80px {t['secondary']}; }} }}
    </style>
    """

    if avatar_type == "Esfera":
        html = css_base + f"""
        <div class="avatar-container">
            <div style="position: relative; width: 200px; height: 200px; animation: float 4s ease-in-out infinite;">
                <img src="{img_esfera}" 
                     style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%; border: 2px solid {t['primary']}; box-shadow: 0 0 40px {t['secondary']}; animation: glow-pulse 3s infinite;" 
                     alt="Esfera">
                <div style="position: absolute; inset: -15px; border-radius: 50%; border: 2px dashed {t['secondary']}; animation: spin 15s linear infinite;"></div>
            </div>
        </div>
        """
    elif avatar_type == "Robô Digital":
        html = css_base + f"""
        <div class="avatar-container">
            <div style="position: relative; width: 180px; height: 240px;">
                <img src="{img_robo}" 
                     style="width: 100%; height: 100%; object-fit: contain; filter: drop-shadow(0 0 15px {t['border']});" 
                     alt="Robô">
                <div style="position: absolute; bottom: 28%; left: 50%; transform: translateX(-50%); width: 40px; height: 40px; background: {t['primary']}; border-radius: 50%; filter: blur(15px); animation: pulse 1.5s infinite;"></div>
            </div>
        </div>
        """
    else:
        html = css_base + f"""
        <div class="avatar-container">
            <div style="position: relative; width: 180px; height: 180px; border-radius: 50%; overflow: hidden; border: 3px solid {t['secondary']}; box-shadow: inset 0 0 30px {t['secondary']}, 0 0 30px {t['primary']};">
                <img src="{img_olho}" 
                     style="width: 100%; height: 100%; object-fit: cover; animation: move-iris 5s infinite alternate ease-in-out;" 
                     alt="Olho Cibernético">
                <div style="position: absolute; top: 0; left: 0; width: 100%; height: 50%; background: {t['bg']}; z-index: 10; transform-origin: top; animation: blink-lid 4s infinite;"></div>
            </div>
        </div>
        """

    components.html(html, height=320)

# ============================================================
# APP PRINCIPAL
# ============================================================
init_db()
if "session_id" not in st.session_state: st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state: st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel")
    avatar_type = st.selectbox("🤖 Avatar do Zeus", ["Olho Cibernético", "Robô Digital", "Esfera"], index=0, key="avatar_sel")
    
    # GROQ COMO PADRÃO
    model_choice = st.selectbox("🧠 Modelo LLM", ["groq", "gemini", "claude"], index=0, key="model_sel")

    st.divider()
    st.caption("Status das Chaves:")
    for k in ["GROQ_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"]: 
        st.write(("✅ " if _secret(k) else "❌ ") + k)

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE")
render_avatar(avatar_type=avatar_type, theme_name=theme_name)

st.subheader("🎙️ Fale com o Zeus")
audio = mic_recorder(start_prompt="🎙️ Clique para Gravar", stop_prompt="⏹️ Clique para Parar", just_once=True, use_container_width=True, key='zeus_mic')

if audio:
    with st.spinner("Transcrevendo áudio..."):
        texto_transcrito = transcrever_audio(audio['bytes'])
        if texto_transcrito and not texto_transcrito.startswith("❌") and not texto_transcrito.startswith("⚠️"):
            st.success(f"Você disse: *{texto_transcrito}*")
            st.session_state.messages.append({"role": "user", "content": texto_transcrito})
            add_message(st.session_state.session_id, "user", texto_transcrito)
            with st.chat_message("user"): st.markdown(texto_transcrito)
            with st.chat_message("assistant"):
                with st.spinner("Processando..."):
                    response = get_llm_response(st.session_state.session_id, texto_transcrito, model_choice)
                    st.markdown(response)
                    add_message(st.session_state.session_id, "assistant", response)
                    st.session_state.messages.append({"role": "assistant", "content": response})
            st.rerun()
        else:
            st.error(texto_transcrito)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.markdown(msg["content"])

if prompt := st.chat_input("Digite o comando para o Zeus..."):
    st.session_state.messages.append({"role": "user", "content": prompt}); add_message(st.session_state.session_id, "user", prompt)
    with st.chat_message("user"): st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Processando..."):
            response = get_llm_response(st.session_state.session_id, prompt, model_choice)
            st.markdown(response); add_message(st.session_state.session_id, "assistant", response); st.session_state.messages.append({"role": "assistant", "content": response})
    st.rerun()
