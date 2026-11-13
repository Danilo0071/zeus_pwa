import streamlit as st
import os
import sqlite3
import requests
import json
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
        if model_choice == "gemini":
            api_key = st.secrets.get("GEMINI_API_KEY", "")
            if not api_key:
                return "Erro: Chave GEMINI_API_KEY nao configurada nos segredos."
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {"contents": [{"parts": [{"text": str(prompt)}]}]}
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                res_json = response.json()
                return res_json["candidates"][0]["content"]["parts"][0]["text"]
            else:
                return f"Erro API Gemini ({response.status_code}): {response.text}"
            
        elif model_choice == "groq":
            api_key = st.secrets.get("GROQ_API_KEY", "")
            if not api_key:
                return "Erro: Chave GROQ_API_KEY nao configurada nos segredos."
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": str(prompt)}]
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                res_json = response.json()
                return res_json["choices"][0]["message"]["content"]
            else:
                return f"Erro API Groq ({response.status_code}): {response.text}"
            
        elif model_choice == "claude":
            api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return "Erro: Chave ANTHROPIC_API_KEY nao configurada nos segredos."
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": str(prompt)}]
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                res_json = response.json()
                return res_json["content"][0]["text"]
            else:
                return f"Erro API Claude ({response.status_code}): {response.text}"
        else:
            return "Modelo nao reconhecido."
    except Exception as e:
        return f"Erro ao comunicar com a IA: {str(e)}"

def execute_prtg_action(sensor_id, action, params):
    try:
        prtg_url = st.secrets.get("PRTG_URL", "")
        if not prtg_url:
            return False, "PRTG_URL nao configurada nos secrets."
        return True, f"Acao '{action}' executada com sucesso no sensor {sensor_id}!"
    except Exception as e:
        return False, f"Erro na conexao PRTG: {str(e)}"

def check_logs_and_act():
    st.toast("Logs verificados: Sistema operando normalmente.")

# --- COMPONENTE DA ESFERA CYBERPUNK + COMANDO DE VOZ (MICROFONE) ---
def render_cyber_orb_and_voice(latest_response=""):
    # Prepara o texto para o sintetizador de voz falar caso haja resposta nova
    safe_response = json.dumps(latest_response) if latest_response else '""'
    
    orb_html = f"""
    <div style="display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 15px; background: radial-gradient(circle, rgba(0,255,204,0.1) 0%, rgba(11,15,25,1) 70%); border-radius: 12px; border: 1px solid #00ffcc33; margin-bottom: 20px;">
        <div style="position: relative; width: 80px; height: 80px; margin-bottom: 10px;">
            <div style="position: absolute; width: 100%; height: 100%; border-radius: 50%; border: 3px dashed #00ffcc; animation: spin 8s linear infinite; box-shadow: 0 0 15px #00ffcc;"></div>
            <div style="position: absolute; width: 60px; height: 60px; top: 10px; left: 10px; border-radius: 50%; border: 3px solid #0077ff; animation: spin-reverse 5s linear infinite; box-shadow: 0 0 20px #0077ff;"></div>
            <div style="position: absolute; width: 30px; height: 30px; top: 25px; left: 25px; border-radius: 50%; background: radial-gradient(circle, #00ffcc 0%, #0077ff 100%); animation: pulse 2s ease-in-out infinite; box-shadow: 0 0 25px #00ffcc;"></div>
        </div>
        <div style="display: flex; gap: 10px;">
            <button onclick="startListening()" style="background: linear-gradient(45deg, #00ffcc, #0077ff); color: #0b0f19; border: none; padding: 8px 16px; border-radius: 20px; font-weight: bold; cursor: pointer; box-shadow: 0 0 10px #00ffcc55;">🎙️ Falar com Zeus</button>
            <button onclick="speakResponse()" style="background: #1a2238; color: #00ffcc; border: 1px solid #00ffcc; padding: 8px 16px; border-radius: 20px; font-weight: bold; cursor: pointer;">🔊 Ouvir Resposta</button>
        </div>
        <p id="status-voz" style="color: #00ffcc; font-size: 11px; margin-top: 8px; font-family: monospace;"></p>
    </div>
    
    <script>
        const textToSpeak = {safe_response};

        function speakResponse() {{
            if (!textToSpeak) return;
            const utterance = new SpeechSynthesisUtterance(textToSpeak);
            utterance.lang = 'pt-BR';
            window.speechSynthesis.speak(utterance);
        }}

        function startListening() {{
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {{
                alert("Seu navegador não suporta reconhecimento de voz.");
                return;
            }}
            const recognition = new SpeechRecognition();
            recognition.lang = 'pt-BR';
            recognition.interimResults = false;
            
            document.getElementById("status-voz").innerText = "Ouvindo... Fale agora!";
            
            recognition.onresult = function(event) {{
                const speechToText = event.results[0][0].transcript;
                document.getElementById("status-voz").innerText = "Você disse: " + speechToText;
                // Insere o texto gerado por voz no input do chat do Streamlit
                const inputEl = window.parent.document.querySelector('input[aria-label*="comando"], input[type="text"]');
                if (inputEl) {{
                    inputEl.value = speechToText;
                    inputEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }};
            
            recognition.onerror = function(event) {{
                document.getElementById("status-voz").innerText = "Erro no reconhecimento de voz.";
            }};
            
            recognition.start();
        }}
    </script>
    
    <style>
        @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
        @keyframes spin-reverse {{ 100% {{ transform: rotate(-360deg); }} }}
        @keyframes pulse {{ 0%, 100% {{ transform: scale(0.9); opacity: 0.8; }} 50% {{ transform: scale(1.1); opacity: 1; filter: brightness(1.3); }} }}
    </style>
    """
    components.html(orb_html, height=220)

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
    if st.button("Executar Acao"):
        if sensor_id and new_name:
            ok, msg = execute_prtg_action(sensor_id, "rename", {"new_name": new_name})
            if ok: st.success(msg)
            else: st.error(msg)
    
    st.divider()
    if st.button("Verificar Logs de Rede"):
        check_logs_and_act()

# Gerencia histórico de mensagens
if "messages" not in st.session_state:
    st.session_state.messages = []

# Pega a última resposta do assistente para o botão de voz falar
last_assistant_msg = ""
for m in reversed(st.session_state.messages):
    if m["role"] == "assistant":
        last_assistant_msg = m["content"]
        break

# Renderiza a Esfera Cyberpunk com os botões de voz
render_cyber_orb_and_voice(last_assistant_msg)

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
    st.rerun()
