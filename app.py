import os
import sqlite3
import tempfile

import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_mic_recorder import mic_recorder
except Exception:  # pacote opcional
    mic_recorder = None

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# TEMAS
# ============================================================
THEMES = {
    "Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"},
}


def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(f"""
        <style>
            .stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {t['panel']}; }}
            h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}
        </style>
    """, unsafe_allow_html=True)


# ============================================================
# BANCO DE DADOS
# ============================================================
DB_FILE = "zeus_chat.db"


def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.commit(); conn.close()
    except Exception:
        pass


def add_message(session_id, role, content):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit(); conn.close()
    except Exception:
        pass


def get_recent_context(session_id, limit=6):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []


# ============================================================
# CHAVES
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
# CLIENTE LLM (GROQ, CLAUDE, GEMINI)
# Os nomes dos modelos mudam com frequência. Cada provedor tenta uma lista
# de modelos em ordem. Para forçar um modelo, crie nos Secrets:
# GROQ_MODEL, GEMINI_MODEL ou ANTHROPIC_MODEL.
# ============================================================
SYS_PROMPT = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico."

MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"],
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5"],
}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL"}


def _normalize(msgs):
    """Garante papéis alternados começando por 'user' (exigência da Anthropic/Gemini)."""
    out = []
    for m in msgs:
        if m["role"] not in ("user", "assistant") or not str(m["content"]).strip():
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + m["content"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _try_models(candidates, fn):
    """Tenta cada modelo; segue para o próximo se o modelo não existir."""
    last = ""
    for model in candidates:
        code, text = fn(model)
        if code == 200:
            return True, text
        last = f"({code}) {text[:250]}"
        if "credit balance" in text.lower() or code in (401, 403, 429):
            break
        if code not in (400, 404):
            break
    return False, last


def _call_groq(api_key, model, msgs):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "system", "content": SYS_PROMPT}] + msgs, "temperature": 0.4},
        timeout=60)
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text


def _call_claude(api_key, model, msgs):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": SYS_PROMPT, "messages": msgs},
        timeout=60)
    if r.status_code == 200:
        return 200, "".join(b.get("text", "") for b in r.json()["content"])
    return r.status_code, r.text


def _call_gemini(api_key, model, msgs):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"systemInstruction": {"parts": [{"text": SYS_PROMPT}]}, "contents": contents},
        timeout=60)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text


def get_llm_response(session_id, prompt, model_choice):
    """Retorna (ok, texto). Erros NÃO entram no histórico do banco."""
    msgs = _normalize(get_recent_context(session_id) + [{"role": "user", "content": prompt}])
    keys = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
    if model_choice not in keys:
        return False, "Modelo não reconhecido."
    api_key = _secret(keys[model_choice])
    if not api_key:
        return False, f"⚠️ {keys[model_choice]} ausente nos Secrets."

    override = _secret(MODEL_SECRET[model_choice])
    candidates = [override] if override else MODELS[model_choice]
    fn = {"groq": _call_groq, "gemini": _call_gemini, "claude": _call_claude}[model_choice]
    try:
        ok, text = _try_models(candidates, lambda m: fn(api_key, m, msgs))
    except Exception as e:
        return False, f"Erro de conexão: {e}"
    if ok:
        return True, text or "(resposta vazia)"
    if "credit balance" in text.lower():
        return False, "❌ Claude: saldo de créditos da Anthropic insuficiente. Adicione créditos em Plans & Billing ou use Groq/Gemini."
    return False, f"❌ Erro {model_choice} {text}"


def processar_prompt(prompt):
    """Registra a pergunta, chama a IA e registra a resposta. Retorna o texto da resposta."""
    sid = st.session_state.session_id
    st.session_state.messages.append({"role": "user", "content": prompt})
    add_message(sid, "user", prompt)
    ok, response = get_llm_response(sid, prompt, st.session_state.get("model_sel", "groq"))
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok:
        add_message(sid, "assistant", response)
    return response


# ============================================================
# TRANSCRIÇÃO DE ÁUDIO (Groq Whisper)
# ============================================================
def transcrever_audio(audio_bytes: bytes) -> str:
    api_key = _secret("GROQ_API_KEY")
    if not api_key:
        return ""
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.webm", audio_bytes, "audio/webm")},
            data={"model": "whisper-large-v3-turbo", "language": "pt"},
            timeout=60)
        if r.status_code == 200:
            return r.json().get("text", "").strip()
        st.error(f"Erro na transcrição ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        st.error(f"Erro na transcrição: {e}")
    return ""


# ============================================================
# AVATARES (OLHO E ROBÔ)
# ============================================================
def render_avatar(avatar_type="Olho Cibernético", theme_name="Cyberpunk"):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    github_url = "https://raw.githubusercontent.com/Danilo0071/zeus_pwa/main/"
    img_olho = f"{github_url}olho.jpg"
    img_robo = f"{github_url}robo.jpg"

    css_base = f"""
    <style>
        body {{ margin: 0; background: transparent; overflow: hidden; display: flex; justify-content: center; align-items: center; height: 100vh; }}
        .avatar-container {{ display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; background: radial-gradient(circle, {t['panel']} 0%, {t['bg']} 80%); border: 1px solid {t['border']}; border-radius: 12px; margin-bottom: 15px; box-shadow: 0 0 20px {t['border']}; height: 260px; position: relative; }}
        @keyframes pulse {{ 0%,100% {{ transform: scale(0.95); opacity: .8; }} 50% {{ transform: scale(1.05); opacity: 1; }} }}
        @keyframes blink-lid {{ 0%, 95%, 100% {{ transform: scaleY(0); }} 98% {{ transform: scaleY(1); }} }}
        @keyframes move-iris {{ 0% {{ transform: translate(0, 0) scale(1); }} 25% {{ transform: translate(15px, -5px) scale(1.05); }} 50% {{ transform: translate(-15px, 5px) scale(1); }} 75% {{ transform: translate(10px, 10px) scale(0.95); }} 100% {{ transform: translate(0, 0) scale(1); }} }}
    </style>
    """

    if avatar_type == "Robô Digital":
        html = css_base + f'<div class="avatar-container"><div style="position: relative; width: 180px; height: 240px;"><img src="{img_robo}" style="width: 100%; height: 100%; object-fit: contain; filter: drop-shadow(0 0 15px {t["border"]});" alt="Robô"><div style="position: absolute; bottom: 28%; left: 50%; transform: translateX(-50%); width: 40px; height: 40px; background: {t["primary"]}; border-radius: 50%; filter: blur(15px); animation: pulse 1.5s infinite;"></div></div></div>'
    else:
        html = css_base + f'<div class="avatar-container"><div style="position: relative; width: 180px; height: 180px; border-radius: 50%; overflow: hidden; border: 3px solid {t["secondary"]}; box-shadow: inset 0 0 30px {t["secondary"]}, 0 0 30px {t["primary"]};"><img src="{img_olho}" style="width: 100%; height: 100%; object-fit: cover; animation: move-iris 5s infinite alternate ease-in-out;" alt="Olho"><div style="position: absolute; top: 0; left: 0; width: 100%; height: 50%; background: {t["bg"]}; z-index: 10; transform-origin: top; animation: blink-lid 4s infinite;"></div></div></div>'

    components.html(html, height=320)


# ============================================================
# ESFERA 3D (componente bidirecional)
# O HTML é gravado numa pasta temporária e carregado com path=
# (declare_component NÃO aceita html=).
# ============================================================
SPHERE_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #030308; font-family: 'Courier New', Courier, monospace; color: #00ffff; }
  #canvas-container { width: 100%; height: 100%; position: absolute; top: 0; left: 0; z-index: 1; }
  .ui-overlay { position: absolute; top: 20px; left: 20px; z-index: 10; pointer-events: none; }
  h1 { font-size: 1.5rem; margin: 0; text-shadow: 0 0 10px #00ffff, 0 0 20px #ff00ff; letter-spacing: 2px; }
  p { font-size: 0.85rem; color: #ff00ff; text-shadow: 0 0 5px #ff00ff; margin: 5px 0 0 0; }
  .chat-container { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); width: 90%; max-width: 600px; background: rgba(3,3,15,0.85); border: 1px solid #00ffff; box-shadow: 0 0 20px rgba(0,255,255,0.3), inset 0 0 15px rgba(255,0,255,0.2); border-radius: 8px; padding: 15px; z-index: 10; backdrop-filter: blur(5px); box-sizing: border-box; }
  .chat-history { height: 130px; overflow-y: auto; font-size: 0.9rem; margin-bottom: 10px; padding-right: 5px; display: flex; flex-direction: column; gap: 8px; }
  .message { padding: 8px 12px; border-radius: 4px; max-width: 85%; line-height: 1.4; white-space: pre-wrap; }
  .user-msg { background: rgba(255,0,255,0.15); border-left: 3px solid #ff00ff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(0,255,255,0.15); border-left: 3px solid #00ffff; color: #00ffff; align-self: flex-start; }
  .input-group { display: flex; gap: 10px; }
  input[type="text"] { flex-grow: 1; min-width: 0; background: rgba(0,0,0,0.6); border: 1px solid #00ffff; color: #00ffff; padding: 10px; border-radius: 4px; font-family: 'Courier New', Courier, monospace; outline: none; box-shadow: inset 0 0 5px rgba(0,255,255,0.5); }
  button { background: linear-gradient(135deg, #00ffff, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 10px 18px; border-radius: 4px; cursor: pointer; text-transform: uppercase; box-shadow: 0 0 10px rgba(0,255,255,0.5); }
  .mic-btn { background: #ff00ff; color: #fff; }
  .mic-btn.listening { background: #ff0000; animation: pulse 1s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(255,0,0,0.7); } 70% { box-shadow: 0 0 0 10px rgba(255,0,0,0); } 100% { box-shadow: 0 0 0 0 rgba(255,0,0,0); } }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="canvas-container"></div>
<div class="ui-overlay">
  <h1>[ CYBER-SPHERE v3.0 ]</h1>
  <p>STATUS: ONLINE // INTERACTIVE NEURAL CORE</p>
</div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <button id="micBtn" class="mic-btn">🎙️</button>
    <input type="text" id="userInput" placeholder="Digite seu comando..." />
    <button id="sendBtn">Enviar</button>
  </div>
</div>
<script>
// ---------- Ponte com o Streamlit ----------
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 680});

let initialized = false, lastReplyId = 0, pendingEl = null;

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (!initialized) {
    initialized = true;
    (a.history || []).forEach(function (m) { addMessage(m.content, m.role === "user" ? "user" : "ai"); });
    lastReplyId = a.reply_id || 0;
    return;
  }
  if (a.reply && a.reply_id !== lastReplyId) {
    lastReplyId = a.reply_id;
    if (pendingEl) { pendingEl.remove(); pendingEl = null; }
    addMessage(a.reply, "ai");
    speakText(a.reply);
  }
});

// ---------- THREE.JS ----------
const container = document.getElementById('canvas-container');
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x030308, 0.03);
const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
camera.position.z = 6;
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
container.appendChild(renderer.domElement);
function onResize() {
  const w = container.clientWidth || window.innerWidth, h = container.clientHeight || window.innerHeight;
  camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h);
}
window.addEventListener('resize', onResize); onResize(); setTimeout(onResize, 300); setTimeout(onResize, 1000);

scene.add(new THREE.AmbientLight(0xffffff, 0.5));
const pointLight1 = new THREE.PointLight(0x00ffff, 3, 50); pointLight1.position.set(5, 5, 5); scene.add(pointLight1);
const pointLight2 = new THREE.PointLight(0xff00ff, 3, 50); pointLight2.position.set(-5, -5, 5); scene.add(pointLight2);

const sphereGroup = new THREE.Group(); scene.add(sphereGroup);
sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2, 64, 64), new THREE.MeshStandardMaterial({ color: 0x111625, metalness: 0.95, roughness: 0.2 })));
const wireMaterial = new THREE.MeshBasicMaterial({ color: 0x00ffff, wireframe: true, transparent: true, opacity: 0.25 });
sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2.03, 32, 32), wireMaterial));

const rings = [];
for (let i = 0; i < 4; i++) {
  const ring = new THREE.Mesh(new THREE.RingGeometry(2.3 + i * 0.3, 2.35 + i * 0.3, 64),
    new THREE.MeshBasicMaterial({ color: i % 2 === 0 ? 0x00ffff : 0xff00ff, side: THREE.DoubleSide, transparent: true, opacity: 0.6 }));
  ring.rotation.x = Math.random() * Math.PI; ring.rotation.y = Math.random() * Math.PI;
  sphereGroup.add(ring); rings.push({ mesh: ring, speedX: (Math.random() - 0.5) * 0.01, speedY: (Math.random() - 0.5) * 0.01 });
}
const particleCount = 500, posArray = new Float32Array(particleCount * 3);
for (let i = 0; i < particleCount * 3; i++) posArray[i] = (Math.random() - 0.5) * 15;
const particlesGeo = new THREE.BufferGeometry(); particlesGeo.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
const particlesMesh = new THREE.Points(particlesGeo, new THREE.PointsMaterial({ size: 0.03, color: 0x00ffff, transparent: true, opacity: 0.8 }));
scene.add(particlesMesh);

const clock = new THREE.Clock();
function animate() {
  requestAnimationFrame(animate);
  const t = clock.getElapsedTime();
  sphereGroup.rotation.y += 0.005; sphereGroup.rotation.x += 0.002;
  rings.forEach(function (r) { r.mesh.rotation.z += r.speedX; r.mesh.rotation.x += r.speedY; });
  wireMaterial.opacity = 0.2 + Math.sin(t * 3) * 0.15;
  pointLight1.intensity = 2 + Math.sin(t * 4); pointLight2.intensity = 2 + Math.cos(t * 4);
  particlesMesh.rotation.y = t * 0.05;
  renderer.render(scene, camera);
}
animate();

// ---------- Chat / voz ----------
function sendToStreamlit() {
  const input = document.getElementById('userInput');
  const text = input.value.trim();
  if (!text) return;
  addMessage(text, 'user');
  pendingEl = addMessage('⏳ processando...', 'ai');
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: text }, dataType: "json" });
  input.value = '';
}
function addMessage(text, sender) {
  const h = document.getElementById('chatHistory');
  const d = document.createElement('div');
  d.classList.add('message'); d.classList.add(sender === 'user' ? 'user-msg' : 'ai-msg');
  d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight;
  return d;
}
function speakText(text) {
  if ('speechSynthesis' in window) {
    const u = new SpeechSynthesisUtterance(text); u.lang = 'pt-BR'; u.pitch = 0.9;
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  }
}
document.getElementById('sendBtn').addEventListener('click', sendToStreamlit);
document.getElementById('userInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') sendToStreamlit(); });

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const recognition = new SR(); recognition.lang = 'pt-BR';
  recognition.onresult = function (event) { document.getElementById('userInput').value = event.results[0][0].transcript; sendToStreamlit(); };
  recognition.onend = function () { document.getElementById('micBtn').classList.remove('listening'); };
  document.getElementById('micBtn').addEventListener('click', function () {
    document.getElementById('micBtn').classList.add('listening');
    try { recognition.start(); } catch (e) {}
  });
}
</script>
</body>
</html>
"""


@st.cache_resource
def _sphere_component():
    pasta = os.path.join(tempfile.gettempdir(), "zeus_sphere_component")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "index.html"), "w", encoding="utf-8") as f:
        f.write(SPHERE_HTML)
    return components.declare_component("zeus_sphere", path=pasta)


def render_sphere_3d():
    comp = _sphere_component()
    ev = comp(
        reply=st.session_state.get("sphere_reply"),
        reply_id=st.session_state.get("sphere_reply_id", 0),
        history=st.session_state.messages[-10:],
        key="sphere_input",
        default=None,
    )
    if ev and ev.get("id") != st.session_state.get("last_sphere_id"):
        st.session_state.last_sphere_id = ev["id"]
        resposta = processar_prompt(ev["text"])
        st.session_state.sphere_reply = resposta
        st.session_state.sphere_reply_id = ev["id"]
        st.rerun()


# ============================================================
# APP PRINCIPAL
# ============================================================
init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel")
    avatar_type = st.selectbox("🤖 Avatar do Zeus", ["Esfera 3D (Cyberpunk)", "Olho Cibernético", "Robô Digital"], index=0, key="avatar_sel")
    model_choice = st.selectbox("🧠 Modelo LLM", ["groq", "gemini", "claude"], index=0, key="model_sel")

    st.divider()
    st.subheader("🔑 Status das Chaves")
    for k in ["GROQ_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"]:
        st.write(("✅ " if _secret(k) else "❌ ") + k)

inject_theme(theme_name)

if avatar_type == "Esfera 3D (Cyberpunk)":
    render_sphere_3d()
else:
    st.title("⚡ ZEUS // CYBER-CORE")
    render_avatar(avatar_type=avatar_type, theme_name=theme_name)

    st.subheader("🎙️ Fale com o Zeus")
    voice_prompt = None
    if mic_recorder is not None:
        audio = mic_recorder(start_prompt="🎙️ Clique para Gravar", stop_prompt="⏹️ Clique para Parar", just_once=True, use_container_width=True, key="zeus_mic")
        if audio:
            texto = transcrever_audio(audio["bytes"])
            if texto:
                st.success(f"Você disse: {texto}")
                voice_prompt = texto
    else:
        st.info("Gravação de voz indisponível (instale streamlit-mic-recorder).")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Digite o comando para o Zeus...") or voice_prompt
    if prompt:
        with st.spinner("Processando..."):
            processar_prompt(prompt)
        st.rerun()
