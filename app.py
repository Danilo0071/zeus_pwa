import os
import json
import sqlite3
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_mic_recorder import mic_recorder
import base64
from streamlit_js_eval import streamlit_js_eval # Ponte para pegar dados do HTML

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# TEMAS (Para os avatares e sidebar)
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
# CLIENTE LLM (GROQ, CLAUDE, GEMINI) + DEBUG DETALHADO
# ============================================================
def get_llm_response(session_id, prompt, model_choice):
    history = get_recent_context(session_id)
    msgs = history + [{"role": "user", "content": prompt}]
    sys_prompt = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico."

    try:
        if model_choice == "groq":
            api_key = _secret("GROQ_API_KEY")
            if not api_key: return "⚠️ GROQ_API_KEY ausente nos Secrets."
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sys_prompt}] + msgs, "temperature": 0.4}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200: return r.json()["choices"][0]["message"]["content"]
            return f"❌ Erro Groq ({r.status_code}): {r.text[:200]}"

        elif model_choice == "claude":
            api_key = _secret("ANTHROPIC_API_KEY")
            if not api_key: return "⚠️ ANTHROPIC_API_KEY ausente nos Secrets."
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            payload = {"model": "claude-3-5-haiku-20241022", "max_tokens": 1024, "system": sys_prompt, "messages": [m for m in msgs if m["role"] != "system"]}
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            if r.status_code == 200: return r.json()["content"][0]["text"]
            return f"❌ Erro Claude ({r.status_code}): {r.text[:200]}"

        elif model_choice == "gemini":
            api_key = _secret("GEMINI_API_KEY")
            if not api_key: return "⚠️ GEMINI_API_KEY ausente nos Secrets."
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
# AVATARES DO GITHUB (OLHO E ROBÔ)
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
# ESFERA 3D (O CÓDIGO QUE VOCÊ MANDOU, ADAPTADO)
# ============================================================
def render_sphere_3d():
    cyberpunk_html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #030308; font-family: 'Courier New', Courier, monospace; color: #00ffff; }
            #canvas-container { width: 100vw; height: 100vh; position: absolute; top: 0; left: 0; z-index: 1; }
            .ui-overlay { position: absolute; top: 20px; left: 20px; z-index: 10; pointer-events: none; }
            h1 { font-size: 1.5rem; margin: 0; text-shadow: 0 0 10px #00ffff, 0 0 20px #ff00ff; letter-spacing: 2px; }
            p { font-size: 0.85rem; color: #ff00ff; text-shadow: 0 0 5px #ff00ff; margin: 5px 0 0 0; }
            .chat-container { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); width: 90%; max-width: 600px; background: rgba(3, 3, 15, 0.85); border: 1px solid #00ffff; box-shadow: 0 0 20px rgba(0, 255, 255, 0.3), inset 0 0 15px rgba(255, 0, 255, 0.2); border-radius: 8px; padding: 15px; z-index: 10; backdrop-filter: blur(5px); }
            .chat-history { height: 120px; overflow-y: auto; font-size: 0.9rem; margin-bottom: 10px; padding-right: 5px; display: flex; flex-direction: column; gap: 8px; }
            .message { padding: 8px 12px; border-radius: 4px; max-width: 85%; line-height: 1.4; }
            .user-msg { background: rgba(255, 0, 255, 0.15); border-left: 3px solid #ff00ff; color: #fff; align-self: flex-end; }
            .ai-msg { background: rgba(0, 255, 255, 0.15); border-left: 3px solid #00ffff; color: #00ffff; align-self: flex-start; }
            .input-group { display: flex; gap: 10px; }
            input[type="text"] { flex-grow: 1; background: rgba(0, 0, 0, 0.6); border: 1px solid #00ffff; color: #00ffff; padding: 10px; border-radius: 4px; font-family: 'Courier New', Courier, monospace; outline: none; box-shadow: inset 0 0 5px rgba(0, 255, 255, 0.5); }
            button { background: linear-gradient(135deg, #00ffff, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 10px 18px; border-radius: 4px; cursor: pointer; text-transform: uppercase; box-shadow: 0 0 10px rgba(0, 255, 255, 0.5); transition: all 0.2s; }
            button:hover { opacity: 0.9; box-shadow: 0 0 20px #ff00ff; transform: scale(1.02); }
            .mic-btn { background: #ff00ff; color: #fff; }
            .mic-btn.listening { background: #ff0000; animation: pulse 1s infinite; }
            @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(255, 0, 0, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(255, 0, 0, 0); } 100% { box-shadow: 0 0 0 0 rgba(255, 0, 0, 0); } }
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
                <button id="sendBtn" onclick="sendToStreamlit()">Enviar</button>
            </div>
        </div>
        <script>
            // --- THREE.JS SETUP (O visual que você mandou) ---
            const container = document.getElementById('canvas-container');
            const scene = new THREE.Scene();
            scene.fog = new THREE.FogExp2(0x030308, 0.03);
            const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
            camera.position.z = 6;
            const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
            renderer.setSize(window.innerWidth, window.innerHeight);
            container.appendChild(renderer.domElement);

            const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
            scene.add(ambientLight);
            const pointLight1 = new THREE.PointLight(0x00ffff, 3, 50); pointLight1.position.set(5, 5, 5); scene.add(pointLight1);
            const pointLight2 = new THREE.PointLight(0xff00ff, 3, 50); pointLight2.position.set(-5, -5, 5); scene.add(pointLight2);

            const sphereGroup = new THREE.Group(); scene.add(sphereGroup);
            const geometry = new THREE.SphereGeometry(2, 64, 64);
            const material = new THREE.MeshStandardMaterial({ color: 0x111625, metalness: 0.95, roughness: 0.2 });
            const coreSphere = new THREE.Mesh(geometry, material); sphereGroup.add(coreSphere);

            const wireGeometry = new THREE.SphereGeometry(2.03, 32, 32);
            const wireMaterial = new THREE.MeshBasicMaterial({ color: 0x00ffff, wireframe: true, transparent: true, opacity: 0.25 });
            const wireSphere = new THREE.Mesh(wireGeometry, wireMaterial); sphereGroup.add(wireSphere);

            const ringCount = 4; const rings = [];
            for (let i = 0; i < ringCount; i++) {
                const ringGeo = new THREE.RingGeometry(2.3 + i * 0.3, 2.35 + i * 0.3, 64);
                const ringMat = new THREE.MeshBasicMaterial({ color: i % 2 === 0 ? 0x00ffff : 0xff00ff, side: THREE.DoubleSide, transparent: true, opacity: 0.6 });
                const ring = new THREE.Mesh(ringGeo, ringMat);
                ring.rotation.x = Math.random() * Math.PI; ring.rotation.y = Math.random() * Math.PI;
                sphereGroup.add(ring); rings.push({ mesh: ring, speedX: (Math.random() - 0.5) * 0.01, speedY: (Math.random() - 0.5) * 0.01 });
            }

            const particlesGeo = new THREE.BufferGeometry(); const particleCount = 500;
            const posArray = new Float32Array(particleCount * 3);
            for(let i = 0; i < particleCount * 3; i++) { posArray[i] = (Math.random() - 0.5) * 15; }
            particlesGeo.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
            const particlesMat = new THREE.PointsMaterial({ size: 0.03, color: 0x00ffff, transparent: true, opacity: 0.8 });
            const particlesMesh = new THREE.Points(particlesGeo, particlesMat); scene.add(particlesMesh);

            let clock = new THREE.Clock();
            function animate() {
                requestAnimationFrame(animate);
                let elapsedTime = clock.getElapsedTime();
                sphereGroup.rotation.y += 0.005; sphereGroup.rotation.x += 0.002;
                rings.forEach(r => { r.mesh.rotation.z += r.speedX; r.mesh.rotation.x += r.speedY; });
                wireMaterial.opacity = 0.2 + Math.sin(elapsedTime * 3) * 0.15;
                pointLight1.intensity = 2 + Math.sin(elapsedTime * 4) * 1;
                pointLight2.intensity = 2 + Math.cos(elapsedTime * 4) * 1;
                particlesMesh.rotation.y = elapsedTime * 0.05;
                renderer.render(scene, camera);
            }
            animate();
            window.addEventListener('resize', () => {
                camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix();
                renderer.setSize(window.innerWidth, window.innerHeight);
            });

            // --- PONTE COM O PYTHON (STREAMLIT) ---
            function sendToStreamlit() {
                const input = document.getElementById('userInput');
                if(input.value.trim() === "") return;
                
                // Envia o texto para o Python capturar
                window.parent.postMessage({
                    isStreamlitMessage: true,
                    type: "streamlit:setComponentValue",
                    value: input.value
                }, "*");
                
                addMessage(input.value, 'user');
                input.value = '';
            }

            function addMessage(text, sender) {
                const chatHistory = document.getElementById('chatHistory');
                const msgDiv = document.createElement('div');
                msgDiv.classList.add('message');
                msgDiv.classList.add(sender === 'user' ? 'user-msg' : 'ai-msg');
                msgDiv.textContent = text;
                chatHistory.appendChild(msgDiv);
                chatHistory.scrollTop = chatHistory.scrollHeight;
            }
            
            // Fala a resposta
            function speakText(text) {
                if ('speechSynthesis' in window) {
                    const utterance = new SpeechSynthesisUtterance(text);
                    utterance.lang = 'pt-BR';
                    utterance.pitch = 0.9;
                    window.speechSynthesis.speak(utterance);
                }
            }

            // Mic
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (SpeechRecognition) {
                const recognition = new SpeechRecognition();
                recognition.lang = 'pt-BR';
                recognition.onresult = (event) => {
                    document.getElementById('userInput').value = event.results[0][0].transcript;
                    sendToStreamlit();
                };
                document.getElementById('micBtn').addEventListener('click', () => {
                    document.getElementById('micBtn').classList.add('listening');
                    recognition.start();
                });
                recognition.onend = () => document.getElementById('micBtn').classList.remove('listening');
            }
        </script>
    </body>
    </html>
    """
    
    # Declara o componente
    comp = components.declare_component("sphere_3d", html=cyberpunk_html)
    
    # Captura o valor enviado pelo JS
    result = comp(default=None, key="sphere_input")
    
    if result:
        st.session_state.messages.append({"role": "user", "content": result})
        add_message(st.session_state.session_id, "user", result)
        
        with st.spinner("Processando na Esfera..."):
            # Pega o modelo escolhido na sidebar
            model_choice = st.session_state.get("model_sel", "groq")
            response = get_llm_response(st.session_state.session_id, result, model_choice)
            
            st.session_state.messages.append({"role": "assistant", "content": response})
            add_message(st.session_state.session_id, "assistant", response)
            
            # Recarrega a página para injetar a resposta
            st.rerun()

# ============================================================
# APP PRINCIPAL
# ============================================================
init_db()
if "session_id" not in st.session_state: st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state: st.session_state.messages = []

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
    # Renderiza a esfera 3D em tela cheia
    render_sphere_3d()
else:
    # Renderiza os avatares normais
    st.title("⚡ ZEUS // CYBER-CORE")
    render_avatar(avatar_type=avatar_type, theme_name=theme_name)
    
    st.subheader("🎙️ Fale com o Zeus")
    audio = mic_recorder(start_prompt="🎙️ Clique para Gravar", stop_prompt="⏹️ Clique para Parar", just_once=True, use_container_width=True, key='zeus_mic')
    if audio:
        texto = transcrever_audio(audio['bytes'])
        st.success(f"Você disse: {texto}")
        
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
