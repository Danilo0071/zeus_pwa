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
    
    payload = {
        "contents": [{
            "parts": [
                {"text": "Transcreva este áudio para texto em português. Retorne apenas a transcrição."},
                {"inline_data": {"mime_type": "audio/webm", "data": audio_b64}}
            ]
        }]
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return f"❌ Erro na transcrição ({r.status_code}): {r.text[:200]}"
    except Exception as e:
        return f"Erro ao transcrever: {e}"

# ============================================================
# CLIENTE LLM (CLAUDE E GEMINI)
# ============================================================
def get_llm_response(session_id, prompt, model_choice):
    history = get_recent_context(session_id)
    msgs = history + [{"role": "user", "content": prompt}]
    sys_prompt = "Você é Zeus, assistente sênior de DevOps e redes. Responda em português, de forma objetiva e técnica."

    try:
        if model_choice == "claude":
            api_key = _secret("ANTHROPIC_API_KEY")
            if not api_key: return "⚠️ ANTHROPIC_API_KEY ausente."
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            payload = {"model": "claude-3-5-haiku-20241022", "max_tokens": 1024, "system": sys_prompt, "messages": [m for m in msgs if m["role"] != "system"]}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()["content"][0]["text"]
            return f"❌ Erro Claude ({r.status_code}): {r.text[:200]}"

        elif model_choice == "gemini":
            api_key = _secret("GEMINI_API_KEY")
            if not api_key: return "⚠️ GEMINI_API_KEY ausente."
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
            contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
            payload = {"systemInstruction": {"parts": [{"text": sys_prompt}]}, "contents": contents}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return f"❌ Erro Gemini ({r.status_code}): {r.text[:200]}"

        return "Modelo não reconhecido."
    except Exception as e:
        return f"Erro de conexão: {e}"

# ============================================================
# AVATARES VISUAIS (OLHO REAL, ROBÔ CYBERPUNK E ESFERA)
# ============================================================
def render_avatar(avatar_type="Olho Cibernético", theme_name="Cyberpunk"):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    
    html = """
    <style>
        body { margin: 0; background: transparent; overflow: hidden; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .avatar-box { display: flex; justify-content: center; align-items: center; width: 100%; height: 260px; }

        @keyframes pulse { 0%,100% { transform: scale(0.95); opacity: .8; } 50% { transform: scale(1.05); opacity: 1; } }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        @keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-15px); } }
        @keyframes pulse-neck { 0% { opacity: 0.4; height: 80px; } 100% { opacity: 1; height: 110px; } }

        /* ------------------------------------------------ */
        /* 1. OLHO CIBERNÉTICO REALISTA                     */
        /* ------------------------------------------------ */
        .eye-container { width: 170px; height: 170px; position: relative; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: #050810; box-shadow: inset 0 0 40px SEC, 0 0 30px BORDER; overflow: hidden; border: 2px solid PRIM; }
        
        .eye-lid-top { position: absolute; top: -5%; width: 110%; height: 55%; background: #0b0f19; z-index: 10; border-bottom-left-radius: 50% 40%; border-bottom-right-radius: 50% 40%; transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1); transform-origin: top center; box-shadow: 0 5px 15px #000; }
        .eye-lid-bottom { position: absolute; bottom: -5%; width: 110%; height: 55%; background: #0b0f19; z-index: 10; border-top-left-radius: 50% 40%; border-top-right-radius: 50% 40%; transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1); transform-origin: bottom center; box-shadow: 0 -5px 15px #000; }
        
        .eye-globe { width: 130px; height: 130px; border-radius: 50%; background: radial-gradient(circle at 30% 30%, #1a2b4c, #02040a); position: relative; overflow: hidden; box-shadow: inset 0 0 20px #000; }
        .eye-iris { width: 80px; height: 80px; border-radius: 50%; background: radial-gradient(circle, SEC 20%, PRIM 70%, #000 100%); position: absolute; top: 25px; left: 25px; box-shadow: 0 0 25px PRIM; transition: transform 0.5s ease-out; }
        .eye-pupil { width: 30px; height: 30px; border-radius: 50%; background: #000; position: absolute; top: 25px; left: 25px; box-shadow: inset 0 0 10px PRIM; }
        .eye-glint { width: 15px; height: 15px; border-radius: 50%; background: #fff; position: absolute; top: 10px; left: 10px; filter: blur(2px); opacity: 0.8; }

        /* ------------------------------------------------ */
        /* 2. ESFERA HOLOGRÁFICA GRANDE                     */
        /* ------------------------------------------------ */
        .orb-scene { position: relative; width: 200px; height: 240px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; }
        .orb-pedestal { position: absolute; bottom: 0px; width: 150px; height: 50px; background: linear-gradient(to top, BG, transparent); border-bottom: 4px solid PRIM; border-radius: 50%; box-shadow: 0 20px 40px BORDER, 0 15px 30px PRIM; transform: rotateX(75deg); z-index: 1; }
        .orb-neck { position: absolute; bottom: 30px; width: 40px; height: 90px; background: linear-gradient(to top, SEC, transparent); filter: blur(8px); opacity: 0.8; z-index: 2; animation: pulse-neck 2s ease-in-out infinite alternate; }
        .hologram-orb { position: absolute; top: 10px; width: 160px; height: 160px; border-radius: 50%; background: radial-gradient(circle, PRIM 10%, transparent 70%); border: 2px solid SEC; z-index: 3; box-shadow: 0 0 50px PRIM, inset 0 0 30px PRIM; animation: float 3s ease-in-out infinite; }
        .orb-ring-outer { position: absolute; top: 20px; width: 180px; height: 180px; border-radius: 50%; border: 2px dashed SEC; animation: spin 12s linear infinite; z-index: 2; box-shadow: 0 0 20px SEC; }
        .orb-core { position: absolute; top: 55px; width: 70px; height: 70px; border-radius: 50%; background: #fff; box-shadow: 0 0 60px PRIM, 0 0 120px SEC; animation: pulse 1.5s infinite; z-index: 4; }

        /* ------------------------------------------------ */
        /* 3. ROBÔ CYBERPUNK (ESTILO OPTIMUS)               */
        /* ------------------------------------------------ */
        .cyber-robot { width: 120px; height: 140px; position: relative; background: linear-gradient(180deg, #1a1a24 0%, #050810 100%); border-radius: 50px 50px 30px 30px; border: 2px solid #333; box-shadow: 0 0 30px BORDER, inset 0 0 20px #000; display: flex; flex-direction: column; align-items: center; padding-top: 25px; }
        .robot-helmet { position: absolute; top: -10px; width: 80px; height: 40px; background: #222; border-radius: 40px 40px 0 0; border: 2px solid #444; border-bottom: none; }
        .robot-visor { width: 90px; height: 35px; background: #000; border-radius: 20px; border: 2px solid SEC; box-shadow: 0 0 20px SEC, inset 0 0 15px SEC; position: relative; overflow: hidden; display: flex; align-items: center; justify-content: center; gap: 15px; }
        .robot-eye { width: 14px; height: 14px; background: PRIM; border-radius: 50%; box-shadow: 0 0 15px PRIM; transition: all 0.1s; }
        .robot-visor-blink { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: #050810; transform: scaleY(0); transform-origin: top; transition: transform 0.15s; }
        .robot-visor-blink.blink { transform: scaleY(1); }
        .robot-neck { width: 40px; height: 25px; background: #111; position: absolute; bottom: -25px; border-radius: 0 0 15px 15px; border: 2px solid #333; border-top: none; box-shadow: inset 0 0 10px #000; }
        .robot-chest { width: 140px; height: 50px; background: linear-gradient(180deg, #1a1a24, #050810); position: absolute; bottom: -75px; border-radius: 25px 25px 10px 10px; border: 2px solid #333; box-shadow: inset 0 0 15px #000; display: flex; justify-content: center; padding-top: 15px; }
        .robot-core { width: 20px; height: 20px; background: SEC; border-radius: 50%; box-shadow: 0 0 25px SEC; animation: pulse 2s infinite; }
        
        .sleeping .eye-lid-top { transform: scaleY(1); }
        .sleeping .eye-lid-bottom { transform: scaleY(1); }

    </style>
    <div class="avatar-box">
    AVATAR_HTML
    </div>
    <script>
        // Lógica do Olho
        const lidTop = document.querySelector(".eye-lid-top");
        const lidBot = document.querySelector(".eye-lid-bottom");
        const iris = document.querySelector(".eye-iris");
        const pupil = document.querySelector(".eye-pupil");
        const glint = document.querySelector(".eye-glint");

        if (lidTop && lidBot) {
            setInterval(() => {
                lidTop.style.transform = "scaleY(1)";
                lidBot.style.transform = "scaleY(1)";
                setTimeout(() => {
                    lidTop.style.transform = "scaleY(0)";
                    lidBot.style.transform = "scaleY(0)";
                }, 200);
            }, Math.random() * 3000 + 3000);
            
            setInterval(() => {
                const x = (Math.random() - 0.5) * 50;
                const y = (Math.random() - 0.5) * 30;
                iris.style.transform = translate(${x}px, ${y}px);
                pupil.style.transform = translate(${x}px, ${y}px);
                glint.style.transform = translate(${x}px, ${y}px);
            }, 2500);
            
            setTimeout(() => {
                lidTop.style.transform = "scaleY(0)";
                lidBot.style.transform = "scaleY(0)";
            }, 100);
        }

        // Lógica do Robô
        const visorBlink = document.querySelector(".robot-visor-blink");
        if (visorBlink) {
            setInterval(() => {
                visorBlink.classList.add('blink');
                setTimeout(() => visorBlink.classList.remove('blink'), 150);
            }, Math.random() * 4000 + 3000);
        }
    </script>
    """

    if avatar_type == "Esfera":
        avatar_html = """
        <div class="orb-scene">
            <div class="orb-pedestal"></div>
            <div class="orb-neck"></div>
            <div class="orb-ring-outer"></div>
            <div class="hologram-orb"></div>
            <div class="orb-core"></div>
        </div>
        """
    elif avatar_type == "Robô Digital":
        avatar_html = """
        <div class="cyber-robot">
            <div class="robot-helmet"></div>
            <div class="robot-visor">
                <div class="robot-eye"></div>
                <div class="robot-eye"></div>
                <div class="robot-visor-blink"></div>
            </div>
            <div class="robot-neck"></div>
            <div class="robot-chest">
                <div class="robot-core"></div>
            </div>
        </div>
        """
    else:
        avatar_html = """
        <div class="eye-container">
            <div class="eye-globe">
                <div class="eye-iris">
                    <div class="eye-pupil"></div>
                    <div class="eye-glint"></div>
                </div>
            </div>
            <div class="eye-lid-top"></div>
            <div class="eye-lid-bottom"></div>
        </div>
        """

    html = html.replace("AVATAR_HTML", avatar_html)
    replacements = {"PRIM": t["primary"], "SEC": t["secondary"], "BG": t["bg"], "PANEL": t["panel"], "BORDER": t["border"]}
    for k, v in replacements.items():
        html = html.replace(k, v)

    components.html(html, height=300)

# ============================================================
# APP
# ============================================================
init_db()
if "session_id" not in st.session_state: st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state: st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel")
    avatar_type = st.selectbox("🤖 Avatar do Zeus", ["Olho Cibernético", "Esfera", "Robô Digital"], index=0, key="avatar_sel")
    model_choice = st.selectbox("🧠 Modelo LLM", ["gemini", "claude"], key="model_sel")

    st.divider()
    st.subheader("🛡️ Agente PRTG")
    sensor_id = st.text_input("ID do Sensor", key="prtg_sensor_id")
    new_name = st.text_input("Novo nome", key="prtg_new_name")
    if st.button("Renomear", key="btn_renomear"):
        st.info("Função PRTG desativada temporariamente para testes.")
    if st.button("🔍 Verificar Logs", key="btn_logs"): st.toast("Logs OK.")

    st.divider()
    st.caption("Chaves configuradas?")
    for k in ["GEMINI_API_KEY", "ANTHROPIC_API_KEY"]: st.write(("✅ " if _secret(k) else "❌ ") + k)

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
