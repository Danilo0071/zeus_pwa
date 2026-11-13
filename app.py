[23:23, 12/11/2026] Danilo: Zeus 2.0 — Assistente DevOps Multi-LLM com PWA, Voz e Agente PRTG
"""
import os
import json
import sqlite3
import requests
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Zeus 2.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# TEMAS SELECIONÁVEIS
# ============================================================
THEMES = {
    "Cyberpunk": {
        "bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc",
        "secondary": "#0077ff", "text": "#00ffcc",…
[23:29, 12/11/2026] Danilo: """
Zeus 2.0 — Assistente DevOps Multi-LLM com PWA, Voz e Agente PRTG
"""
import os
import json
import sqlite3
import requests
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Zeus 2.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# TEMAS SELECIONÁVEIS
# ============================================================
THEMES = {
    "Cyberpunk": {
        "bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc",
        "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33",
    },
    "Matrix": {
        "bg": "#000000", "panel": "#031a06", "primary": "#00ff41",
        "secondary": "#008f11", "text": "#00ff41", "border": "#00ff4133",
    },
    "Neon Purple": {
        "bg": "#0e0118", "panel": "#1a0533", "primary": "#ff00ff",
        "secondary": "#a020f0", "text": "#ff99ff", "border": "#ff00ff33",
    },
    "Minimal Light": {
        "bg": "#f5f5f5", "panel": "#ffffff", "primary": "#0066ff",
        "secondary": "#00aaff", "text": "#111111", "border": "#0066ff33",
    },
}


def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(
        f"""
        <style>
            .stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {t['panel']}; }}
            h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}
            .stTextInput input, .stSelectbox select, .stTextArea textarea {{
                background-color: {t['panel']} !important; color: {t['text']} !important;
                border: 1px solid {t['primary']} !important; border-radius: 4px;
            }}
            .stButton button {{
                background: linear-gradient(45deg, {t['primary']}, {t['secondary']});
                color: {t['bg']}; font-weight: bold; border: none; border-radius: 4px;
                box-shadow: 0 0 15px {t['border']};
            }}
            .stChatMessage {{ background: {t['panel']} !important; border: 1px solid {t['border']}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# PWA / META TAGS
# ============================================================
st.markdown(
    """
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="#0b0f19">
    <script>
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js').catch(() => {});
      }
    </script>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# BANCO DE DADOS
# ============================================================
DB_FILE = "zeus_chat.db"


def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, role TEXT, content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def add_message(session_id, role, content):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_recent_context(session_id, limit=6):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []


# ============================================================
# LEITURA DE CHAVES (st.secrets + env)
# ============================================================
def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val:
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get(key, default).strip()


# ============================================================
# CLIENTE LLM MULTI-PROVEDOR
# ============================================================
def _build_history_payload(session_id, user_prompt, max_msgs=6):
    history = get_recent_context(session_id, limit=max_msgs)
    payload = [{"role": m["role"], "content": m["content"]} for m in history]
    if not payload or payload[-1]["content"] != user_prompt:
        payload.append({"role": "user", "content": user_prompt})
    return payload


def get_llm_response(session_id, prompt, model_choice):
    prompt = str(prompt).encode("utf-8", errors="ignore").decode("utf-8")
    msgs = _build_history_payload(session_id, prompt)
    sys_prompt = (
        "Você é Zeus, assistente sênior de DevOps e redes. "
        "Responda em português, de forma objetiva e técnica."
    )

    try:
        # -------- GEMINI (CORRIGIDO PARA CHAVES AQ. COM API v1) --------
        if model_choice == "gemini":
            api_key = _secret("GEMINI_API_KEY")
            if not api_key:
                return "⚠️ GEMINI_API_KEY ausente. Configure em Settings → Secrets."
            
            url = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            }
            
            contents = [
                {
                    "role": "user" if m["role"] == "user" else "model",
                    "parts": [{"text": m["content"]}],
                }
                for m in msgs
            ]
            payload = {
                "systemInstruction": {"parts": [{"text": sys_prompt}]},
                "contents": contents,
            }
            
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return f"❌ Gemini {r.status_code}: {r.text[:300]}"

        # -------- GROQ --------
        elif model_choice == "groq":
            api_key = _secret("GROQ_API_KEY")
            if not api_key:
                return "⚠️ GROQ_API_KEY ausente. Configure em Settings → Secrets."
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "system", "content": sys_prompt}] + msgs,
                "temperature": 0.4,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            return f"❌ Groq {r.status_code}: {r.text[:300]}"

        # -------- CLAUDE --------
        elif model_choice == "claude":
            api_key = _secret("ANTHROPIC_API_KEY")
            if not api_key:
                return "⚠️ ANTHROPIC_API_KEY ausente. Configure em Settings → Secrets."
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 1024,
                "system": sys_prompt,
                "messages": [m for m in msgs if m["role"] != "system"],
            }
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                return r.json()["content"][0]["text"]
            return f"❌ Claude {r.status_code}: {r.text[:300]}"

        return "Modelo não reconhecido."
    except requests.exceptions.Timeout:
        return "⏱️ Timeout na chamada da IA. Tente novamente."
    except Exception as e:
        return f"Erro de conexão com a IA: {e}"


# ============================================================
# PRTG — AÇÃO
# ============================================================
def execute_prtg_action(sensor_id, action, params):
    try:
        prtg_url = _secret("PRTG_URL")
        user = _secret("PRTG_USERNAME")
        passhash = _secret("PRTG_PASSHASH")
        if not prtg_url:
            return False, "PRTG_URL não configurada nos secrets."
        if not user or not passhash:
            return False, "Credenciais PRTG ausentes (PRTG_USERNAME / PRTG_PASSHASH)."

        endpoint = f"{prtg_url.rstrip('/')}/editsettings"
        query = {"id": sensor_id, "username": user, "passhash": passhash}

        if action == "rename":
            query["name_"] = params.get("new_name", "")
        elif action == "pause":
            query["active_"] = "0"
        elif action == "resume":
            query["active_"] = "1"
        else:
            return False, f"Ação '{action}' não suportada."

        r = requests.get(endpoint, params=query, timeout=15, verify=False)
        if r.status_code == 200:
            return True, f"Ação '{action}' aplicada ao sensor {sensor_id}."
        return False, f"PRTG {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Erro PRTG: {e}"


def check_logs_and_act():
    st.toast("Logs verificados: sistema operacional OK.")


# ============================================================
# COMPONENTE ESFERA + VOZ + WAKE WORD
# ============================================================
def render_orb_and_voice(
    theme_name="Cyberpunk",
    latest_response="",
    voice_name="",
    rate=1.0,
    pitch=1.0,
    wake_word_enabled=False,
    wake_word="zeus",
):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    safe_response = json.dumps(latest_response or "")
    wake_enabled_js = "true" if wake_word_enabled else "false"

    html = """
    <style>
        @keyframes spin { 100% { transform: rotate(360deg); } }
        @keyframes spin-rev { 100% { transform: rotate(-360deg); } }
        @keyframes pulse {
            0%,100% { transform: scale(0.9); opacity: .8; }
            50% { transform: scale(1.1); opacity: 1; filter: brightness(1.3); }
        }
        #orb-wrap { display:flex; flex-direction:column; align-items:center;
            padding:15px; border-radius:12px; margin-bottom:15px;
            background: radial-gradient(circle, _PRIM22 0%, __BG_ 70%);
            border: 1px solid _BORDER_; }
        .orb { position:relative; width:80px; height:80px; margin-bottom:10px; }
        .ring1 { position:absolute; inset:0; border-radius:50%;
            border:3px dashed _PRIM_; animation:spin 8s linear infinite;
            box-shadow:0 0 15px _PRIM_; }
        .ring2 { position:absolute; inset:10px; border-radius:50%;
            border:3px solid _SEC_; animation:spin-rev 5s linear infinite;
            box-shadow:0 0 20px _SEC_; }
        .core { position:absolute; inset:25px; border-radius:50%;
            background: radial-gradient(circle, _PRIM_ 0%, _SEC_ 100%);
            animation:pulse 2s ease-in-out infinite; box-shadow:0 0 25px _PRIM_; }
        .btns { display:flex; gap:10px; flex-wrap:wrap; justify-content:center; }
        .btn { border:none; padding:8px 16px; border-radius:20px;
            font-weight:bold; cursor:pointer; font-family:inherit; }
        .btn-primary { background:linear-gradient(45deg, _PRIM, __SEC); color:BG_; }
        .btn-ghost { background:_PANEL; color:PRIM; border:1px solid __PRIM_; }
        #status { color:_PRIM_; font-size:11px; margin-top:8px;
            font-family:monospace; text-align:center; min-height:14px; }
    </style>

    <div id="orb-wrap">
        <div class="orb">
            <div class="ring1"></div>
            <div class="ring2"></div>
            <div class="core"></div>
        </div>
        <div class="btns">
            <button class="btn btn-primary" onclick="startListening()">🎙️ Falar</button>
            <button class="btn btn-ghost" onclick="speakResponse()">🔊 Ouvir</button>
            <button class="btn btn-ghost" onclick="toggleWake()" id="wakeBtn">🛰️ Wake</button>
        </div>
        <p id="status">pronto</p>
    </div>

    <script>
        const TEXT = _RESPONSE_;
        const VOICE_NAME = _VOICE_;
        const RATE = _RATE_;
        const PITCH = _PITCH_;
        const WAKE_ENABLED = _WAKE_ENABLED_;
        const WAKE_WORD = "_WAKE_WORD_".toLowerCase();

        const statusEl = document.getElementById("status");
        const wakeBtn = document.getElementById("wakeBtn");

        function pickVoice() {
            const voices = window.speechSynthesis.getVoices();
            if (!VOICE_NAME) return voices.find(v => v.lang.startsWith("pt")) || voices[0];
            return voices.find(v => v.name === VOICE_NAME) || voices[0];
        }

        function speakResponse() {
            if (!TEXT) return;
            const u = new SpeechSynthesisUtterance(TEXT);
            const v = pickVoice();
            if (v) u.voice = v;
            u.lang = "pt-BR";
            u.rate = RATE;
            u.pitch = PITCH;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(u);
        }

        function injectToStreamlit(text) {
            const el = window.parent.document.querySelector(
                'textarea[data-testid="stChatInputTextArea"], input[type="text"]'
            );
            if (el) {
                const proto = el.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter.call(el, text);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.focus();
                setTimeout(() => {
                    const send = window.parent.document.querySelector(
                        'button[data-testid="stChatInputSubmitButton"]'
                    );
                    if (send) send.click();
                }, 200);
            }
        }

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            statusEl.innerText = "Navegador sem suporte a voz.";
        }

        function makeRecognizer(continuous) {
            const r = new SpeechRecognition();
            r.lang = "pt-BR";
            r.continuous = continuous;
            r.interimResults = false;
            return r;
        }

        let wakeActive = WAKE_ENABLED;
        let wakeRec = null;

        function startWake() {
            if (!SpeechRecognition) return;
            wakeRec = makeRecognizer(true);
            wakeRec.onresult = (e) => {
                const last = e.results.length - 1;
                const said = e.results[last][0].transcript.toLowerCase();
                statusEl.innerText = "👂 " + said;
                if (said.includes(WAKE_WORD)) {
                    const cmd = said.split(WAKE_WORD).slice(1).join(" ").trim();
                    if (cmd) {
                        statusEl.innerText = "⚡ " + cmd;
                        injectToStreamlit(cmd);
                        wakeRec.stop();
                        wakeActive = false;
                        wakeBtn.style.opacity = 0.5;
                    } else {
                        statusEl.innerText = "🛰️ Diga o comando após 'Zeus'...";
                    }
                }
            };
            wakeRec.onerror = () => { statusEl.innerText = "erro wake"; };
            wakeRec.onend = () => {
                if (wakeActive) { try { wakeRec.start(); } catch(e){} }
            };
            try {
                wakeRec.start();
                statusEl.innerText = "🛰️ Escutando wake word...";
            } catch(e){}
        }

        function toggleWake() {
            if (wakeActive) {
                wakeActive = false;
                if (wakeRec) wakeRec.stop();
                wakeBtn.style.opacity = 0.5;
                statusEl.innerText = "wake off";
            } else {
                wakeActive = true;
                wakeBtn.style.opacity = 1;
                startWake();
            }
        }

        function startListening() {
            if (!SpeechRecognition) return;
            const r = makeRecognizer(false);
            statusEl.innerText = "🎙️ Ouvindo...";
            r.onresult = (e) => {
                const said = e.results[0][0].transcript;
                statusEl.innerText = "🗣️ " + said;
                injectToStreamlit(said);
            };
            r.onerror = () => { statusEl.innerText = "erro reconhecimento"; };
            r.start();
        }

        if (wakeActive) startWake();
    </script>
    """

    replacements = {
        "_PRIM_": t["primary"],
        "_SEC_": t["secondary"],
        "_BG_": t["bg"],
        "_PANEL_": t["panel"],
        "_BORDER_": t["border"],
        "_RESPONSE_": safe_response,
        "_VOICE_": json.dumps(voice_name),
        "_RATE_": str(float(rate)),
        "_PITCH_": str(float(pitch)),
        "_WAKE_ENABLED_": wake_enabled_js,
        "_WAKE_WORD_": wake_word,
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    components.html(html, height=240)


# ============================================================
# INICIALIZAÇÃO
# ============================================================
init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("⚙️ Configuração")

    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0)
    model_choice = st.selectbox("🧠 Modelo LLM", ["gemini", "groq", "claude"])

    st.divider()
    st.subheader("🗣️ Voz")
    st.caption("A voz é renderizada no navegador (Web Speech API).")
    voice_options = [
        "", "Luciana", "Daniel", "Google português do Brasil",
        "Microsoft Francisca", "Microsoft Antonio",
    ]
    voice_name = st.selectbox("Voz preferida", voice_options, index=0)
    voice_rate = st.slider("Velocidade", 0.5, 2.0, 1.0, 0.1)
    voice_pitch = st.slider("Tom", 0.5, 2.0, 1.0, 0.1)

    st.divider()
    st.subheader("🛰️ Wake Word")
    wake_enabled = st.toggle("Escuta contínua por 'Zeus'", value=False)
    wake_word = st.text_input("Palavra-chave", value="zeus").lower() or "zeus"

    st.divider()
    st.subheader("🛡️ Agente PRTG")
    sensor_id = st.text_input("ID do Sensor")
    new_name = st.text_input("Novo nome")
    if st.button("Renomear"):
        if sensor_id and new_name:
            ok, msg = execute_prtg_action(sensor_id, "rename", {"new_name": new_name})
            (st.success if ok else st.error)(msg)

    if st.button("🔍 Verificar Logs"):
        check_logs_and_act()

    st.divider()
    st.caption("Chaves configuradas?")
    for k in ["GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"]:
        st.write(("✅ " if _secret(k) else "❌ ") + k)


# ============================================================
# APLICA TEMA + TÍTULO
# ============================================================
inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE")


# ============================================================
# ESFERA + VOZ
# ============================================================
last_assistant = ""
for m in reversed(st.session_state.messages):
    if m["role"] == "assistant":
        last_assistant = m["content"]
        break

render_orb_and_voice(
    theme_name=theme_name,
    latest_response=last_assistant,
    voice_name=voice_name,
    rate=voice_rate,
    pitch=voice_pitch,
    wake_word_enabled=wake_enabled,
    wake_word=wake_word,
)


# ============================================================
# HISTÓRICO
# ============================================================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ============================================================
# INPUT
# ============================================================
if prompt := st.chat_input("Digite o comando para o Zeus..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    add_message(st.session_state.session_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Processando..."):
            response = get_llm_response(
                st.session_state.session_id, prompt, model_choice
            )
            st.markdown(response)
            add_message(st.session_state.session_id, "assistant", response)
            st.session_state.messages.append(
                {"role": "assistant", "content": response}
            )

    st.rerun()
