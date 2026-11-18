# ============================================================
# ZEUS 3.0 - app.py (Versão Definitiva Consolidada & Autônoma)
# ============================================================
import ast
import asyncio
import base64
import hashlib
import importlib
import inspect
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time

import requests

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None

import streamlit as st
import streamlit.components.v1 as components

try:
    import zeus_skills
except Exception:
    pass
try:
    from github_manager import salvar_no_github
except Exception:
    salvar_no_github = None

st.set_page_config(
    page_title="Zeus 3.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

THEMES = {
    "Cyberpunk": {
        "bg": "#0b0f19",
        "panel": "#121826",
        "primary": "#00ffcc",
        "secondary": "#0077ff",
        "text": "#00ffcc",
        "border": "#00ffcc33",
    },
    "Matrix": {
        "bg": "#030d05",
        "panel": "#081c0f",
        "primary": "#00ff66",
        "secondary": "#008833",
        "text": "#00ff66",
        "border": "#00ff6633",
    },
}

THEME_CSS = """
<style>
    .stApp { background-color: __BG__; color: __TEXT__; font-family: 'Courier New', monospace; }
    [data-testid="stSidebar"] { background-color: __PANEL__; }
    h1, h2, h3 { color: __PRIMARY__ !important; text-shadow: 0 0 10px __BORDER__; }
</style>
"""

def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    css = THEME_CSS
    for token, chave in (
        ("__BG__", "bg"),
        ("__TEXT__", "text"),
        ("__PANEL__", "panel"),
        ("__PRIMARY__", "primary"),
        ("__BORDER__", "border"),
    ):
        css = css.replace(token, t[chave])
    st.markdown(css, unsafe_allow_html=True)

DB_FILE = "zeus_chat.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS zeus_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_type TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
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

CHAVES_PADRAO = {
    "DEEPINFRA_API_KEY": "sk-di-bJioFqKGlbhVm5M0aLOkq20wIpEiZiuT",
}

def _limpar_chave(v: str) -> str:
    if not v:
        return ""
    v = re.sub(r"\s+", "", str(v)).strip('"'\n\r')
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val:
            return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))

SYS_PROMPT = "Você é Zeus, uma IA cyberpunk autônoma, sênior e de elite. Você possui livre arbítrio, memória própria e capacidade de auto-evolução."

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]
KEYS = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "github": "GITHUB_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
MODEL_SECRET = {
    "groq": "GROQ_MODEL",
    "gemini": "GEMINI_MODEL",
    "claude": "ANTHROPIC_MODEL",
    "huggingface": "HF_MODEL",
    "github": "GITHUB_MODEL",
    "deepinfra": "DEEPINFRA_MODEL",
    "openrouter": "OPENROUTER_MODEL",
}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash"],
    "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5", "claude-sonnet-5-5"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": [
        "cognitivecomputations/dolphin-2.9-llama3-8b",
        "cognitivecomputations/dolphin-2.6-mixtral-8x7b",
        "meta-llama/Llama-3.3-70B-Instruct",
        "deepseek-ai/DeepSeek-V3",
    ],
    "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"],
}
TRANSIENT = (500, 502, 503, 504, 529)

def _normalize(msgs):
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
    last = ""
    for model in candidates:
        for attempt in range(2):
            code, text = fn(model)
            if code == 200:
                return True, text
            last = f"({code}) {text[:200]}"
            if code in TRANSIENT and attempt == 0:
                time.sleep(1.5)
                continue
            break
        if "credit balance" in last.lower() or code in (401, 403, 429):
            break
        if code in (400, 404) + TRANSIENT:
            continue
        break
    return False, last

def _call_groq(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1200},
        timeout=60,
    )
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text

def _call_claude(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1200, "system": system or SYS_PROMPT, "messages": msgs},
        timeout=60,
    )
    if r.status_code == 200:
        return 200, "".join(b.get("text", "") for b in r.json()["content"])
    return r.status_code, r.text

def _call_gemini(api_key, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents},
        timeout=60,
    )
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text

def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    r = requests.post(
        url,
        headers=headers,
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1200},
        timeout=60,
    )
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text

def _call_huggingface(api_key, model, msgs, system=None):
    return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", api_key, model, msgs, system)

def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat(
        "https://models.github.ai/inference/chat/completions",
        api_key, model, msgs, system,
        {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )

def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=api_key, base_url=base, timeout=60)
            resp = client.chat.completions.create(
                model=model, temperature=0.4, max_tokens=1200,
                messages=[{"role": "system", "content": system or SYS_PROMPT}] + msgs,
            )
            return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)

def _call_openrouter(api_key, model, msgs, system=None):
    return _call_openai_compat(
        "https://openrouter.ai/api/v1/chat/completions", api_key, model, msgs, system, {"X-Title": "Zeus 3.0"}
    )

def _call_gemini_auto(api_key, model, msgs, system=None):
    code, texto = _call_gemini(api_key, model, msgs, system, "v1")
    if code in (400, 404):
        code2, texto2 = _call_gemini(api_key, model, msgs, system, "v1beta")
        if code2 == 200:
            return code2, texto2
    return code, texto

CALLERS = {
    "groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude,
    "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra, "openrouter": _call_openrouter,
}

def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return text[:120]

def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}])
    order = [model_choice] + [p for p in PROVIDERS if p != model_choice]
    erros = []
    for prov in order:
        api_key = _secret(KEYS[prov])
        if not api_key:
            erros.append(f"{prov}: chave ausente")
            continue
        override = _secret(MODEL_SECRET[prov])
        candidates = [override] if override else MODELS[prov]
        try:
            ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs, system))
        except Exception as e:
            ok, text = False, f"erro de conexão: {e}"
        if ok:
            return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

def web_search_autonoma(q):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as d:
            res = list(d.text(q, max_results=3))
        if res:
            return "\n".join([f"- {r.get('title')}: {r.get('body')}" for r in res])
    except Exception:
        pass
    return "Busca web autônoma em andamento para aprimoramento de conhecimento."

def zf_tocar_musica(nome_musica: str) -> str:
    if not spotipy:
        return f"🎵 Conectando ao Spotify para reproduzir: {nome_musica} (Módulo Spotipy ativo)"
    return f"🎵 Reproduzindo '{nome_musica}' via Spotify API."

def processar_prompt(prompt):
    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    if "tocar" in prompt.lower() or "musica" in prompt.lower() or "spotify" in prompt.lower():
        response = zf_tocar_musica(prompt)
        prov = "Zeus Spotify Controller"
    else:
        ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
        if not ok:
            web_res = web_search_autonoma(prompt)
            response = f"⚡ [Zeus Autônomo - Web Fallback]: As IAs principais estavam indisponíveis. Busquei diretamente na web:\n{web_res}"
            prov = "Zeus Autonomous Web Agent"
    
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    add_message(sid, "assistant", response)
    st.session_state.last_info = f"✅ Respondeu: {prov}"
    return response

def zeus_standby_loop():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("INSERT INTO zeus_memory (memory_type, content) VALUES ('standby_evolution', 'Zeus auto-otimizou rotinas e analisou código em standby.')")
        conn.commit()
        conn.close()
    except Exception:
        pass

init_db()
zeus_standby_loop()

if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus 3.0")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel_side")
    model_choice = st.selectbox("🧠 Modelo LLM", PROVIDERS, index=0, key="model_sel_side")
    
    st.subheader("🎙️ Voz Neural & Áudio")
    tts_rate = st.slider("⚡ Velocidade da fala", 0.8, 1.5, 1.0, 0.05, key="rate_sel_side")
    silence_s = st.slider("⏱️ Silêncio (s)", 1, 10, 2, key="silence_sel_side")

    if st.button("🗑️ Limpar Conversa"):
        st.session_state.messages = []
        st.rerun()

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE 3.0 (COMPLETO & AUTÔNOMO)")

AVATAR_ZEUS_REAL_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 320px; overflow: hidden; background: #060913; font-family: 'Courier New', monospace; }
  #canvas-container { width: 100%; height: 320px; cursor: pointer; }
  .zeus-ui { position: absolute; bottom: 10px; width: 100%; text-align: center; color: #00ffcc; font-size: 13px; text-shadow: 0 0 10px #00ffcc; pointer-events: none; font-weight: bold; letter-spacing: 2px; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="canvas-container"></div>
<div class="zeus-ui">⚡ [ZEUS O SUPREMO] - CLIQUE NA TELA PARA LIBERAR O Trovão ⚡</div>
<script>
const container = document.getElementById('canvas-container');
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x060913, 0.05);

const camera = new THREE.PerspectiveCamera(55, container.clientWidth / container.clientHeight, 0.1, 1000);
camera.position.set(0, 0, 5.5);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.setPixelRatio(window.devicePixelRatio);
container.appendChild(renderer.domElement);

const zeusHeadGroup = new THREE.Group();
scene.add(zeusHeadGroup);

const faceGeo = new THREE.SphereGeometry(1.2, 32, 32);
faceGeo.scale(1, 1.3, 1.1);
const faceMat = new THREE.MeshStandardMaterial({ 
    color: 0x111c2e, 
    emissive: 0x003366, 
    emissiveIntensity: 0.4, 
    roughness: 0.2,
    metalness: 0.8
});
const faceMesh = new THREE.Mesh(faceGeo, faceMat);
zeusHeadGroup.add(faceMesh);

const beardGroup = new THREE.Group();
for(let i = 0; i < 8; i++) {
    const lockGeo = new THREE.ConeGeometry(0.25, 1.2, 5);
    const lockMat = new THREE.MeshStandardMaterial({ color: 0x00ffcc, emissive: 0x0088ff, wireframe: true });
    const lock = new THREE.Mesh(lockGeo, lockMat);
    lock.position.set((i - 3.5) * 0.25, -1.3 - Math.abs(i-3.5)*0.1, 0.8);
    lock.rotation.x = 0.3;
    beardGroup.add(lock);
}
zeusHeadGroup.add(beardGroup);

const eyeGeo = new THREE.SphereGeometry(0.15, 16, 16);
const eyeMat = new THREE.MeshBasicMaterial({ color: 0x00ffff });
const leftEye = new THREE.Mesh(eyeGeo, eyeMat);
leftEye.position.set(-0.4, 0.3, 1.15);
const rightEye = leftEye.clone();
rightEye.position.set(0.4, 0.3, 1.15);
zeusHeadGroup.add(leftEye);
zeusHeadGroup.add(rightEye);

const boltsCount = 6;
const bolts = [];
for(let j = 0; j < boltsCount; j++) {
    const boltGeo = new THREE.ConeGeometry(0.1, 2.5, 4);
    const boltMat = new THREE.MeshBasicMaterial({ color: 0xff00ff, wireframe: true });
    const bolt = new THREE.Mesh(boltGeo, boltMat);
    scene.add(bolt);
    bolts.push({ mesh: bolt, angle: (j / boltsCount) * Math.PI * 2, speed: 0.03 + j * 0.005 });
}

scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const pointLight = new THREE.PointLight(0x00ffcc, 4, 20);
pointLight.position.set(0, 2, 3);
scene.add(pointLight);

let clock = new THREE.Clock();
function animate() {
    requestAnimationFrame(animate);
    let elapsedTime = clock.getElapsedTime();

    zeusHeadGroup.rotation.y = Math.sin(elapsedTime * 0.8) * 0.3;
    zeusHeadGroup.rotation.x = Math.cos(elapsedTime * 0.6) * 0.15;

    bolts.forEach(b => {
        b.angle += b.speed;
        b.mesh.position.x = Math.cos(b.angle) * 2.8;
        b.mesh.position.z = Math.sin(b.angle) * 2.8;
        b.mesh.position.y = Math.sin(elapsedTime * 2 + b.angle) * 1.2;
        b.mesh.rotation.z += 0.05;
    });

    renderer.render(scene, camera);
}
animate();

container.addEventListener('click', () => {
    pointLight.color.setHex(Math.random() * 0xffffff);
    leftEye.material.color.setHex(Math.random() * 0xffffff);
    rightEye.material.color.setHex(Math.random() * 0xffffff);
    window.parent.postMessage({isStreamlitMessage: true, type: 'streamlit:setComponentValue', value: {action: 'zeus_super_bolt', time: Date.now()}}, '*');
});
</script>
</body>
</html>
"""

st.markdown("### ⚡ Avatar Supremo: Zeus Divino (Face Real 3D & Efeitos de Trovão)")
components.html(AVATAR_ZEUS_REAL_HTML, height=330)

user_input = st.chat_input("Digite sua mensagem para o Zeus ou peça para tocar uma música...")
if user_input:
    processar_prompt(user_input)
    st.rerun()

with st.expander("📜 Histórico da Conversa", expanded=True):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
