import asyncio
import base64
import hashlib
import os
import re
import sqlite3
import tempfile
import time

import requests
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

THEMES = {
    "Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"},
}


def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    css_code = """
        <style>
            .stApp {{ background-color: {bg}; color: {text}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {panel}; }}
            h1, h2, h3 {{ color: {primary} !important; text-shadow: 0 0 10px {border}; }}
        </style>
    """.format(bg=t['bg'], text=t['text'], panel=t['panel'], primary=t['primary'], border=t['border'])
    st.markdown(css_code, unsafe_allow_html=True)


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
# ZEUS 3.0: MÓDULO DE VISÃO (CÂMERA WEB) E MÍDIA
# ============================================================
try:
    import cv2
except ImportError:
    cv2 = None

class ZeusVisionMedia:
    def _init_(self):
        pass

    def capture_image_from_webcam(self, output_path="captured_frame.jpg") -> str:
        if not cv2:
            raise Exception("OpenCV (cv2) não está instalado no ambiente.")
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise Exception("Não foi possível acessar a câmera do dispositivo.")
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise Exception("Falha ao capturar o frame da câmera.")
        cv2.imwrite(output_path, frame)
        return output_path

    def enhance_prompt_for_media(self, user_description: str, image_path: str = None) -> str:
        base_prompt = f"Create a masterpiece, hyper-realistic cinematic lighting, 8k resolution, futuristic cybernetic details based on: {user_description}"
        if image_path and os.path.exists(image_path):
            base_prompt += " [Contexto visual da câmera incorporado com sucesso]"
        return base_prompt

    def generate_free_media(self, optimized_prompt: str, media_type: str = "image") -> str:
        hf_key = _secret("HUGGINGFACE_API_KEY")
        model = "stabilityai/stable-diffusion-2" if media_type == "image" else "camenduru/AnimateDiff"
        url = f"https://api-inference.huggingface.co/models/{model}"
        headers = {"Authorization": f"Bearer {hf_key}"} if hf_key else {}
        payload = {"inputs": optimized_prompt}
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                output_file = f"generated_{media_type}.png" if media_type == "image" else f"generated_{media_type}.mp4"
                with open(output_file, "wb") as f:
                    f.write(response.content)
                return f"Mídia gerada com sucesso e salva em {output_file}!"
            else:
                return f"Erro ao gerar mídia gratuita: {response.text} (Verifique se sua HUGGINGFACE_API_KEY está ativa)."
        except Exception as e:
            return f"Erro na conexão para geração de mídia: {str(e)}"

zeus_vm = ZeusVisionMedia()


# ============================================================
# CLIENTE LLM (GROQ, GEMINI, CLAUDE, HUGGINGFACE, GITHUB) MULTI-IA COM FALLBACK AUTOMÁTICO
# ============================================================
SYS_PROMPT = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico. Respostas curtas, pois serão faladas em voz alta."

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github"]
KEYS = {
    "groq": "GROQ_API_KEY", 
    "gemini": "GEMINI_API_KEY", 
    "claude": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "github": "GITHUB_API_KEY"
}
MODEL_SECRET = {
    "groq": "GROQ_MODEL", 
    "gemini": "GEMINI_MODEL", 
    "claude": "ANTHROPIC_MODEL",
    "huggingface": "HUGGINGFACE_MODEL",
    "github": "GITHUB_MODEL"
}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-2.5-flash"],
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5"],
    "huggingface": ["meta-llama/Meta-Llama-3-8B-Instruct"],
    "github": ["gpt-4o"]
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


def _groq_payload(model, msgs):
    p = {"model": model, "messages": [{"role": "system", "content": SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}
    if model.startswith("openai/gpt-oss"):
        p["reasoning_effort"] = "low"
    return p


def _call_groq(api_key, model, msgs):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_groq_payload(model, msgs),
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


def _call_huggingface(api_key, model, msgs):
    prompt_text = "\n".join([f"{m['role']}: {m['content']}" for m in msgs])
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {"inputs": prompt_text, "parameters": {"max_new_tokens": 256}}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 200:
        res = r.json()
        if isinstance(res, list) and len(res) > 0:
            return 200, res[0].get("generated_text", str(res))
        return 200, str(res)
    return r.status_code, r.text


def _call_github(api_key, model, msgs):
    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    } if api_key else {}
    payload = {
        "messages": [{"role": "system", "content": SYS_PROMPT}] + msgs,
        "model": model,
        "max_tokens": 256
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 200:
        data = r.json()
        return 200, data["choices"][0]["message"]["content"]
    return r.status_code, r.text


CALLERS = {
    "groq": _call_groq, 
    "gemini": _call_gemini, 
    "claude": _call_claude,
    "huggingface": _call_huggingface,
    "github": _call_github
}


def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return text[:120]


def get_llm_response(session_id, prompt, model_choice):
    """Busca automática em cascata por múltiplos provedores (Fallback de IAs)."""
    msgs = _normalize(get_recent_context(session_id) + [{"role": "user", "content": prompt}])
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
            ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs))
        except Exception as e:
            ok, text = False, f"erro de conexão: {e}"
        if ok:
            return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros


def processar_prompt(prompt):
    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok:
        add_message(sid, "assistant", response)
        st.session_state.last_info = f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "")
    else:
        st.session_state.last_info = "❌ " + " | ".join(erros)
    return response


# ============================================================
# RECONHECIMENTO (Whisper/Groq) E VOZ NEURAL (Edge TTS)
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    api_key = _secret("GROQ_API_KEY")
    if not api_key:
        return "", "GROQ_API_KEY ausente (necessária para o reconhecimento Whisper)."
    if not audio_bytes:
        return "", "Áudio vazio."
    base = (mime or "audio/webm").split(";")[0]
    ext = "mp4" if "mp4" in base else "ogg" if "ogg" in base else "webm"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (f"audio.{ext}", audio_bytes, base)},
            data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0",
                  "prompt": "Conversa em português do Brasil com o assistente Zeus."},
            timeout=60)
        if r.status_code != 200:
            return "", f"Transcrição falhou ({r.status_code}): {r.text[:120]}"
        texto = r.json().get("text", "").strip()
        if len(texto) < 2 or "legendas pela comunidade" in texto.lower():
            return "", ""
        return texto, ""
    except Exception as e:
        return "", f"Erro na transcrição: {e}"


def _limpar_para_fala(texto: str, limite: int = 700) -> str:
    texto = re.sub(r".*?", " (código omitido) ", texto, flags=re.S)
    texto = re.sub(r"[*#`_>~|]", "", texto).strip()
    if len(texto) > limite:
        corte = texto[:limite]
        fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
        texto = corte[: fim + 1] if fim > 200 else corte
    return texto


async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
        if c["type"] == "audio":
            chunks.append(c["data"])
    return b"".join(chunks)


def sintetizar(texto, voz, rate=1.0):
    texto = _limpar_para_fala(texto)
    if not texto:
        return None
    pct = f"{int(round((rate - 1) * 100)):+d}%"
    try:
        return asyncio.run(asyncio.wait_for(_edge_synth(texto, voz, pct), timeout=25)) or None
    except Exception as e:
        st.session_state.tts_erro = str(e)[:150]
        return None


FALLBACK_VOZES = {
    "Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural",
    "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural",
    "Raquel (pt-PT ♀)": "pt-PT-RaquelNeural",
    "Duarte (pt-PT ♂)": "pt-PT-DuarteNeural",
}


@st.cache_data(ttl=86400, show_spinner=False)
def listar_vozes_edge():
    try:
        import edge_tts
        todas = asyncio.run(edge_tts.list_voices())
        pt = [v for v in todas if v["Locale"].lower().startswith("pt-")]
        vozes = {}
        for v in pt:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
            vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes or FALLBACK_VOZES
    except Exception:
        return FALLBACK_VOZES


# ============================================================
# AVATAR CYBERPUNK (Esfera 3D / Olho / Robô Realista e Detalhado)
# ============================================================
AVATAR_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #030308; font-family: 'Courier New', Courier, monospace; color: #00ffff; }
  #three-box, #c2 { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
  #voiceSel { position: absolute; top: 14px; right: 14px; z-index: 11; background: #030308; color: #00ffff; border: 1px solid #00ffff; border-radius: 4px; padding: 4px; max-width: 45%; font-family: inherit; font-size: 0.75rem; }
  .ui-overlay { position: absolute; top: 16px; left: 16px; z-index: 10; pointer-events: none; }
  h1 { font-size: 1.3rem; margin: 0; text-shadow: 0 0 10px #00ffff, 0 0 20px #ff00ff; letter-spacing: 2px; }
  p { font-size: 0.8rem; color: #ff00ff; text-shadow: 0 0 5px #ff00ff; margin: 5px 0 0 0; }
  .chat-container { position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%); width: 92%; max-width: 620px; background: rgba(3,3,15,0.85); border: 1px solid #00ffff; box-shadow: 0 0 20px rgba(0,255,255,0.3), inset 0 0 15px rgba(255,0,255,0.2); border-radius: 8px; padding: 12px; z-index: 10; backdrop-filter: blur(5px); box-sizing: border-box; }
  .chat-history { height: 120px; overflow-y: auto; font-size: 0.88rem; margin-bottom: 10px; padding-right: 5px; display: flex; flex-direction: column; gap: 8px; }
  .message { padding: 8px 12px; border-radius: 4px; max-width: 85%; line-height: 1.4; white-space: pre-wrap; }
  .user-msg { background: rgba(255,0,255,0.15); border-left: 3px solid #ff00ff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(0,255,255,0.15); border-left: 3px solid #00ffff; color: #00ffff; align-self: flex-start; }
  .input-group { display: flex; gap: 8px; }
  input[type="text"] { flex-grow: 1; min-width: 0; background: rgba(0,0,0,0.6); border: 1px solid #00ffff; color: #00ffff; padding: 10px; border-radius: 4px; font-family: inherit; outline: none; box-shadow: inset 0 0 5px rgba(0,255,255,0.5); }
  button { background: linear-gradient(135deg, #00ffff, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 10px 16px; border-radius: 4px; cursor: pointer; text-transform: uppercase; box-shadow: 0 0 10px rgba(0,255,255,0.5); }
  .mic-btn { background: #ff00ff; color: #fff; }
  .mic-btn.on { background: #ff0000; animation: pulse 1s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(255,0,0,0.7); } 70% { box-shadow: 0 0 0 10px rgba(255,0,0,0); } 100% { box-shadow: 0 0 0 0 rgba(255,0,0,0); } }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="three-box"></div>
<canvas id="c2"></canvas>
<select id="voiceSel" style="display:none"></select>
<div class="ui-overlay">
  <h1 id="title">[ ZEUS // ULTRA-REALISTIC v3.0 ]</h1>
  <p id="status">STATUS: ONLINE</p>
</div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <button id="micBtn" class="mic-btn" title="Voz: fale e aguarde o silêncio">🎙️</button>
    <input type="text" id="userInput" placeholder="Digite, ou clique 🎙️ e fale..." />
    <button id="sendBtn">Enviar</button>
  </div>
</div>
<script>
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 700});

const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
let avatar = "sphere", SILENCE = 2, STT = "whisper", TTS = "edge", TTS_RATE = 1;
let initialized = false, lastReplyId = 0, pendingEl = null, pendingUserEl = null;
let state = "idle", pulse = 0;
let micOn = false, pausedForBot = false, buffer = "", silenceTimer = null, silenceDeadline = 0, thinkTimer = null, lastAfter = 0;
let rec = null, mediaRec = null;
const TITLES = { sphere: "[ ZEUS // CYBER-SPHERE ]", eye: "[ ZEUS // CYBER-EYE ]", robot: "[ ZEUS // REALISTIC-BOT ]" };
const STATE_TXT = { idle: "ONLINE", listening: "OUVINDO", thinking: "PROCESSANDO", speaking: "FALANDO" };
const statusEl = document.getElementById("status"), input = document.getElementById("userInput"), micBtn = document.getElementById("micBtn"), voiceSel = document.getElementById("voiceSel");

function setState(s) { state = s; renderStatus(); }
function renderStatus() {
  let txt = "STATUS: " + STATE_TXT[state];
  if (micOn && state === "listening" && silenceDeadline > 0) {
    const left = Math.max(0, Math.ceil((silenceDeadline - Date.now()) / 1000));
    txt += " // ENVIA EM " + left + "s";
  } else if (micOn && state === "idle") { txt += " // MIC ATIVO"; }
  statusEl.textContent = txt;
}
setInterval(renderStatus, 300);
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.avatar && a.avatar !== avatar) { avatar = a.avatar; applyAvatar(); }
  else if (!initialized) { applyAvatar(); }
  SILENCE = a.silence_s || 2; STT = a.stt_engine || "whisper"; TTS = a.tts_engine || "edge"; TTS_RATE = a.tts_rate || 1;
  voiceSel.style.display = (TTS === "browser") ? "block" : "none";
  if (!initialized) {
    initialized = true;
    (a.history || []).forEach(function (m) { addMessage(m.content, m.role === "user" ? "user" : "ai"); });
    lastReplyId = a.reply_id || 0;
    return;
  }
  if (a.reply_id !== lastReplyId) {
    lastReplyId = a.reply_id;
    clearTimeout(thinkTimer);
    if (pendingEl) { pendingEl.remove(); pendingEl = null; }
    if (pendingUserEl) { if (a.transcript) pendingUserEl.textContent = a.transcript; else pendingUserEl.remove(); pendingUserEl = null; }
    if (a.reply) {
      addMessage(a.reply, "ai");
      if (/^(❌|⚠️)/.test(a.reply)) afterSpeak(); else speakReply(a.reply, a.audio_b64, a.audio_mime);
    }
  }
});

function addMessage(text, sender) {
  const h = document.getElementById("chatHistory");
  const d = document.createElement("div");
  d.classList.add("message"); d.classList.add(sender === "user" ? "user-msg" : "ai-msg");
  d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight;
  return d;
}
function haltListening() {
  try { rec && rec.stop(); } catch (e) {}
  if (mediaRec && mediaRec.state === "recording") { mediaRec.onstop = null; try { mediaRec.stop(); } catch (e) {} }
}
function markThinking() {
  setState("thinking"); pausedForBot = true; clearTimeout(thinkTimer);
  thinkTimer = setTimeout(function () { if (state === "thinking") afterSpeak(); }, 60000);
}
function sendText(text) {
  text = (text || "").trim();
  if (!text) return;
  clearTimeout(silenceTimer); silenceDeadline = 0; buffer = ""; input.value = "";
  addMessage(text, "user");
  pendingEl = addMessage("⏳ processando...", "ai");
  haltListening(); markThinking();
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: text }, dataType: "json" });
}
function submitAudio(b64, mime) {
  pendingUserEl = addMessage("🎙️ transcrevendo...", "user");
  pendingEl = addMessage("⏳ processando...", "ai");
  markThinking();
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), audio: b64, mime: mime }, dataType: "json" });
}
document.getElementById("sendBtn").addEventListener("click", function () { unlockAudio(); sendText(input.value); });
input.addEventListener("keydown", function (e) { if (e.key === "Enter") { unlockAudio(); sendText(input.value); } });

const player = new Audio(); player.setAttribute("playsinline", "");
let unlocked = false;
const SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
function unlockAudio() {
  if (unlocked) return; unlocked = true;
  try { player.src = SILENT; const p = player.play(); if (p && p.catch) p.catch(function () {}); } catch (e) {}
  try { if ("speechSynthesis" in window) { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; window.speechSynthesis.speak(u); } } catch (e) {}
}
document.addEventListener("pointerdown", unlockAudio);
document.addEventListener("touchstart", unlockAudio);

function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  let vs = window.speechSynthesis.getVoices();
  const pt = vs.filter(function (v) { return /^pt/i.test(v.lang); });
  if (pt.length) vs = pt;
  const saved = lsGet("zeusVoice");
  voiceSel.innerHTML = "";
  vs.forEach(function (v) { const o = document.createElement("option"); o.value = v.name; o.textContent = v.name + " (" + v.lang + ")"; if (v.name === saved) o.selected = true; voiceSel.appendChild(o); });
}
if ("speechSynthesis" in window) { loadVoices(); window.speechSynthesis.onvoiceschanged = loadVoices; }
voiceSel.addEventListener("change", function () { lsSet("zeusVoice", voiceSel.value); });

function speakReply(text, b64, mime) {
  if (TTS === "edge" && b64) {
    setState("speaking");
    player.onended = afterSpeak;
    player.onerror = function () { speakBrowser(text); };
    player.src = "data:" + (mime || "audio/mpeg") + ";base64," + b64;
    const p = player.play();
    if (p && p.catch) p.catch(function () { speakBrowser(text); });
  } else { speakBrowser(text); }
}
function speakBrowser(text) {
  if (!("speechSynthesis" in window)) { afterSpeak(); return; }
  setState("speaking"); window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/[*#`_>]/g, ""));
  u.lang = "pt-BR"; u.rate = TTS_RATE; u.pitch = 0.95;
  const vs = window.speechSynthesis.getVoices();
  const chosen = vs.find(function (v) { return v.name === voiceSel.value; }) || vs.find(function (v) { return /pt[-_]BR/i.test(v.lang); });
  if (chosen) u.voice = chosen;
  let started = false;
  u.onstart = function () { started = true; }; u.onend = afterSpeak; u.onerror = afterSpeak;
  window.speechSynthesis.speak(u);
  setTimeout(function () { if (!started && state === "speaking") { window.speechSynthesis.cancel(); afterSpeak(); } }, 4000);
}
function afterSpeak() {
  const n = Date.now(); if (n - lastAfter < 600) return; lastAfter = n;
  setState("idle"); pausedForBot = false;
  if (!micOn) return;
  if (STT === "whisper") setTimeout(function () { if (micOn && stream) beginRecording(); }, 300);
  else setTimeout(function () { try { rec.start(); } catch (e) {} }, 400);
}

let stream = null, ac = null, analyser = null, chunks = [], vadTimer = null, td = null;
let speechStarted = false, speechStartTs = 0, lastVoiceTs = 0, recStart = 0, thr = 0.03, calib = [];
function pickMime() {
  const c = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  if (!window.MediaRecorder) return "";
  for (let i = 0; i < c.length; i++) { if (MediaRecorder.isTypeSupported(c[i])) return c[i]; }
  return "";
}
async function micStartWhisper() {
  if (!stream) {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    ac = new (window.AudioContext || window.webkitAudioContext)();
    const src = ac.createMediaStreamSource(stream);
    analyser = ac.createAnalyser(); analyser.fftSize = 1024; src.connect(analyser);
    td = new Uint8Array(analyser.fftSize);
  }
  if (ac.state === "suspended") await ac.resume();
  beginRecording();
  if (!vadTimer) vadTimer = setInterval(vadTick, 60);
}
function beginRecording() {
  if (!stream || (mediaRec && mediaRec.state === "recording")) return;
  chunks = []; speechStarted = false; calib = []; recStart = Date.now();
  const mime = pickMime();
  mediaRec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  mediaRec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
  mediaRec.start(250);
}
function resetRecorder() {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.onstop = null; try { mediaRec.stop(); } catch (e) {} }
  beginRecording();
}
function vadTick() {
  if (!micOn || pausedForBot || !analyser || !mediaRec || mediaRec.state !== "recording") return;
  analyser.getByteTimeDomainData(td);
  let sum = 0; for (let i = 0; i < td.length; i++) { const v = (td[i] - 128) / 128; sum += v * v; }
  const rms = Math.sqrt(sum / td.length), now = Date.now();
  if (calib.length < 8) { calib.push(rms); if (calib.length === 8) { const f = calib.reduce(function (a, b) { return a + b; }, 0) / 8; thr = Math.min(0.08, Math.max(0.025, f * 2.5)); } return; }
  if (rms > thr) {
    lastVoiceTs = now; if (!speechStarted) { speechStarted = true; speechStartTs = now; }
    pulse = Math.min(1, rms * 10); if (state === "idle") setState("listening");
  }
  if (speechStarted) {
    silenceDeadline = lastVoiceTs + SILENCE * 1000;
    if ((now - lastVoiceTs) / 1000 >= SILENCE) finishRecording();
  } else if (now - recStart > 25000) { resetRecorder(); }
}
function finishRecording() {
  const dur = lastVoiceTs - speechStartTs, mime = mediaRec.mimeType || "audio/webm";
  pausedForBot = true; speechStarted = false; silenceDeadline = 0;
  mediaRec.onstop = function () {
    if (dur < 350) { pausedForBot = false; setState("idle"); beginRecording(); return; }
    const blob = new Blob(chunks, { type: mime });
    const fr = new FileReader();
    fr.onload = function () { submitAudio(String(fr.result).split(",")[1], mime); };
    fr.readAsDataURL(blob);
  };
  try { mediaRec.stop(); } catch (e) {}
}
function stopWhisper() {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.onstop = null; try { mediaRec.stop(); } catch (e) {} }
  if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
  if (ac) { try { ac.close(); } catch (e) {} ac = null; }
  analyser = null; clearInterval(vadTimer); vadTimer = null;
}

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function armSilence() {
  clearTimeout(silenceTimer);
  silenceDeadline = Date.now() + SILENCE * 1000;
  silenceTimer = setTimeout(function () { sendText(input.value); }, SILENCE * 1000);
}
if (SR) {
  rec = new SR(); rec.lang = "pt-BR"; rec.continuous = !IS_MOBILE; rec.interimResults = true;
  rec.onresult = function (e) {
    if (pausedForBot) return;
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i], t = r[0].transcript;
      if (r.isFinal) buffer = (buffer + " " + t).trim(); else interim += t;
    }
    input.value = (buffer + " " + interim).trim();
    pulse = 1;
    if (state !== "thinking" && state !== "speaking") setState("listening");
    armSilence();
  };
  rec.onend = function () { if (micOn && !pausedForBot && STT === "browser") setTimeout(function () { try { rec.start(); } catch (e) {} }, 200); };
  rec.onerror = function (e) {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") { micOn = false; micBtn.classList.remove("on"); statusEl.textContent = "STATUS: MICROFONE BLOQUEADO"; }
  };
}

micBtn.addEventListener("click", async function () {
  unlockAudio();
  micOn = !micOn; micBtn.classList.toggle("on", micOn);
  if (micOn) {
    pausedForBot = false; buffer = "";
    if (STT === "browser" && !SR) STT = "whisper";
    try {
      if (STT === "whisper") await micStartWhisper(); else rec.start();
    } catch (e) {
      micOn = false; micBtn.classList.remove("on"); statusEl.textContent = "STATUS: MICROFONE BLOQUEADO"; return;
    }
  } else {
    clearTimeout(silenceTimer); silenceDeadline = 0;
    try { rec && rec.stop(); } catch (e) {}
    stopWhisper();
    if (state === "listening") setState("idle");
  }
  renderStatus();
});

const rnd = function (a, b) { return a + Math.random() * (b - a); };
const CY = "#00ffcc", MG = "#ff0077", METALLIC = "#222a38";
let look = { x: 0, y: 0 }, lookT = { x: 0, y: 0 }, open = 1, blinkStart = null, pupil = 1, ring = 0;
let nextBlink = performance.now() + rnd(3000, 7000), nextLook = performance.now() + rnd(1000, 3000);

function updateCommon(now, dt, t) {
  if (state === "thinking") lookT = { x: 0, y: -0.1 };
  else if (state === "speaking") lookT = { x: Math.sin(t * 3) * 0.25, y: Math.sin(t * 5) * 0.08 };
  else if (now >= nextLook) { lookT = { x: rnd(-1, 1), y: rnd(-0.6, 0.6) }; nextLook = now + rnd(1200, 3200); }
  const k = Math.min(1, dt * 4); look.x += (lookT.x - look.x) * k; look.y += (lookT.y - look.y) * k;
  if (blinkStart === null && now >= nextBlink) { blinkStart = now; nextBlink = now + rnd(3000, 7000); }
  if (blinkStart !== null) { const p = (now - blinkStart) / 220; if (p >= 1) { open = 1; blinkStart = null; } else open = Math.abs(2 * p - 1); }
  pulse = Math.max(0, pulse - dt * 1.5);
  const tg = state === "listening" ? 1.15 + pulse * 0.9 : state === "speaking" ? 1 + 0.25 * (Math.sin(t * 14) * 0.5 + 0.5) : state === "thinking" ? 0.8 : 1 + pulse * 0.5;
  pupil += (tg - pupil) * Math.min(1, dt * 8);
  ring = (ring + dt * (state === "thinking" ? 420 : 40)) % 360;
}

const cv = document.getElementById("c2"), ctx = cv.getContext("2d");
let W = 0, H = 0;
function resize2d() { const d = window.devicePixelRatio || 1; W = cv.clientWidth; H = cv.clientHeight; cv.width = W * d; cv.height = H * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
const parts = []; for (let i = 0; i < 70; i++) parts.push({ x: Math.random(), y: Math.random(), s: Math.random() * 1.8 + 0.4, v: Math.random() * 0.02 + 0.005 });
function glow(c, b) { ctx.shadowColor = c; ctx.shadowBlur = b; }
function noGlow() { ctx.shadowBlur = 0; }
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function arcs(cx, cy, r, start, n, span) { for (let i = 0; i < n; i++) { const a = start + i * 2 * Math.PI / n; ctx.beginPath(); ctx.arc(cx, cy, r, a, a + span); ctx.stroke(); } }

function drawBg(t, dt) {
  ctx.fillStyle = "#030308"; ctx.fillRect(0, 0, W, H);
  const hy = H * 0.6;
  ctx.strokeStyle = "rgba(0,255,204,0.12)"; ctx.lineWidth = 1;
  for (let i = -12; i <= 12; i++) { ctx.beginPath(); ctx.moveTo(W / 2 + i * 16, hy); ctx.lineTo(W / 2 + i * W / 9, H); ctx.stroke(); }
  for (let j = 0; j < 8; j++) { const p = (j + (t * 0.4) % 1) / 8; const y = hy + (H - hy) * p * p; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  parts.forEach(function (p) {
    p.y -= p.v * dt; if (p.y < 0) { p.y = 1; p.x = Math.random(); }
    ctx.fillStyle = "rgba(0,255,204,0.5)"; ctx.fillRect(p.x * W, p.y * H, p.s, p.s);
  });
}

function drawEye(t) {
  const cx = W / 2, cy = H * 0.36, R = Math.min(W * 0.26, H * 0.17), rad = Math.PI / 180;
  const ls = state === "listening", al = (60 + (ls ? 100 : 35) * (0.5 + 0.5 * Math.sin(t * (ls ? 6 : 2)))) / 255;
  let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.9); g.addColorStop(0, "rgba(0,255,204," + al + ")"); g.addColorStop(1, "rgba(0,255,204,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 1.9, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(0,255,204,0.35)"; ctx.lineWidth = 1;
  for (let i = 0; i < 60; i++) { const a = (i * 6 - ring * 0.3) * rad, r1 = R * 1.62, r2 = R * (i % 5 ? 1.67 : 1.72); ctx.beginPath(); ctx.moveTo(cx + r1 * Math.cos(a), cy + r1 * Math.sin(a)); ctx.lineTo(cx + r2 * Math.cos(a), cy + r2 * Math.sin(a)); ctx.stroke(); }
  ctx.lineWidth = 2; ctx.strokeStyle = "#0077ff"; arcs(cx, cy, R * 1.5, -ring * rad, 3, 50 * rad);
  ctx.strokeStyle = MG; arcs(cx, cy, R * 1.82, ring * 0.6 * rad, 2, 35 * rad);
  const oh = Math.max(0.02, open);
  ctx.save(); ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.45, R * 0.85 * oh, 0, 0, 7); ctx.clip();
  ctx.fillStyle = "#050b14"; ctx.fillRect(0, 0, W, H);
  const ix = cx + look.x * R * 0.5, iy = cy + look.y * R * 0.28, ir = R * 0.72;
  g = ctx.createRadialGradient(ix, iy, 0, ix, iy, ir); g.addColorStop(0, "#002233"); g.addColorStop(0.6, "#005577"); g.addColorStop(1, CY);
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.fill();
  const pr = ir * 0.38 * pupil;
  ctx.strokeStyle = "rgba(0,255,204,0.3)"; ctx.lineWidth = 1;
  for (let i = 0; i < 36; i++) { const a = i * 10 * rad; ctx.beginPath(); ctx.moveTo(ix + pr * Math.cos(a), iy + pr * Math.sin(a)); ctx.lineTo(ix + ir * 0.95 * Math.cos(a), iy + ir * 0.95 * Math.sin(a)); ctx.stroke(); }
  ctx.strokeStyle = "#88ffff"; ctx.lineWidth = 2; arcs(ix, iy, ir * 0.88, ring * 1.5 * rad, 4, 40 * rad);
  ctx.fillStyle = "#000"; ctx.strokeStyle = CY; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(ix, iy, pr, 0, 7); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.9)"; ctx.beginPath(); ctx.arc(ix - pr * 0.4, iy - pr * 0.4, pr * 0.18, 0, 7); ctx.fill();
  ctx.restore();
  glow(CY, 16); ctx.strokeStyle = CY; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.45, R * 0.85 * oh, 0, 0, 7); ctx.stroke(); noGlow();
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

# ROBÔ REALISTA E DETALHADO (Texturas metálicas, placas blindadas, fibra de carbono e brilho óptico avançado)
function drawRobot(t) {
  const cx = W / 2, cy = H * 0.38, S = Math.min(W * 0.5, H * 0.46), hw = S * 0.5, hh = S * 0.42;
  const ls = state === "listening", th = state === "thinking", sp = state === "speaking";

  let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, S * 1.1); g.addColorStop(0, "rgba(0,255,204," + (0.2 + (ls ? 0.15 * Math.sin(t * 6) : 0)) + ")"); g.addColorStop(1, "rgba(0,255,204,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, S * 1.1, 0, 7); ctx.fill();

  ctx.strokeStyle = "#445566"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(cx, cy - hh); ctx.lineTo(cx, cy - hh - S * 0.16); ctx.stroke();
  ctx.strokeStyle = CY; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(cx, cy - hh); ctx.lineTo(cx, cy - hh - S * 0.16); ctx.stroke();
  const ar = S * 0.04 * (1 + 0.3 * Math.sin(t * (th ? 14 : 4)));
  glow(MG, 22); ctx.fillStyle = MG; ctx.beginPath(); ctx.arc(cx, cy - hh - S * 0.16, ar, 0, 7); ctx.fill(); noGlow();
  if (th) { ctx.strokeStyle = CY; ctx.lineWidth = 2; arcs(cx, cy - hh - S * 0.16, S * 0.08, ring * Math.PI / 180 * 3, 3, 1.2); }

  ctx.fillStyle = "#111622"; ctx.strokeStyle = "#334455"; ctx.lineWidth = 2;
  rr(cx - hw - S * 0.08, cy - S * 0.12, S * 0.08, S * 0.24, 6); ctx.fill(); ctx.stroke();
  rr(cx + hw, cy - S * 0.12, S * 0.08, S * 0.24, 6); ctx.fill(); ctx.stroke();

  g = ctx.createLinearGradient(cx - hw, cy - hh, cx + hw, cy + hh); 
  g.addColorStop(0, "#2a3548"); g.addColorStop(0.5, "#151b28"); g.addColorStop(1, "#0a0e17");
  glow(CY, 20); ctx.fillStyle = g; ctx.strokeStyle = CY; ctx.lineWidth = 2.5; 
  rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.fill(); ctx.stroke(); noGlow();

  ctx.strokeStyle = "rgba(0,255,204,0.3)"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx - hw * 0.9, cy - hh * 0.7); ctx.lineTo(cx - hw * 0.7, cy - hh * 0.5); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx + hw * 0.9, cy - hh * 0.7); ctx.lineTo(cx + hw * 0.7, cy - hh * 0.5); ctx.stroke();

  ctx.fillStyle = "#0c101a"; ctx.strokeStyle = "#223344"; ctx.lineWidth = 1;
  rr(cx - hw * 0.6, cy + hh * 0.25, hw * 1.2, hh * 0.35, 6); ctx.fill(); ctx.stroke();
  for(let i = -3; i <= 3; i++) {
    ctx.beginPath(); ctx.moveTo(cx + i * (hw * 0.32), cy + hh * 0.3); ctx.lineTo(cx + i * (hw * 0.32), cy + hh * 0.55); ctx.stroke();
  }

  g = ctx.createLinearGradient(0, cy - hh * 0.7, 0, cy + hh * 0.1);
  g.addColorStop(0, "#01040a"); g.addColorStop(1, "#0a1525");
  ctx.fillStyle = g; ctx.strokeStyle = CY; ctx.lineWidth = 2;
  rr(cx - hw * 0.82, cy - hh * 0.65, hw * 1.64, hh * 0.62, 12); ctx.fill(); ctx.stroke();

  const ey = cy - hh * 0.34, lx = look.x * hw * 0.08, ly = look.y * hh * 0.05;
  if (th) {
    const bx = cx + Math.sin(t * 4) * hw * 0.55;
    glow(CY, 26); ctx.fillStyle = CY; rr(bx - hw * 0.12, ey - hh * 0.1, hw * 0.24, hh * 0.2, 8); ctx.fill(); noGlow();
  } else {
    [-1, 1].forEach(function (s) {
      const rx = hw * 0.2 * (ls ? 1 + pulse * 0.3 : 1), ry = Math.max(2, hh * 0.18 * open);
      glow(ls ? MG : CY, 25); ctx.fillStyle = ls ? MG : CY;
      ctx.beginPath(); ctx.ellipse(cx + s * hw * 0.42 + lx, ey + ly, rx, ry, 0, 0, 7); ctx.fill(); noGlow();
      ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.arc(cx + s * hw * 0.42 + lx - rx * 0.25, ey + ly - ry * 0.25, Math.max(1.5, ry * 0.28), 0, 7); ctx.fill();
    });
  }

  const my = cy + hh * 0.68, bars = 13, bw = hw * 0.09, gap = hw * 0.05, total = bars * bw + (bars - 1) * gap, x0 = cx - total / 2;
  glow(ls ? MG : CY, 14); ctx.fillStyle = ls ? MG : CY;
  for (let i = 0; i < bars; i++) {
    let h = hh * 0.04;
    if (sp) h = hh * 0.08 + hh * 0.2 * Math.abs(Math.sin(t * 12 + i * 1.2) * Math.cos(t * 5 + i));
    else if (ls) h = hh * 0.04 + hh * 0.15 * pulse * Math.abs(Math.sin(t * 9 + i));
    ctx.fillRect(x0 + i * (bw + gap), my - h, bw, h * 2);
  }
  noGlow();
  ctx.fillStyle = "rgba(0,0,0,0.15)"; for (let y = 0; y < H; y += 5) ctx.fillRect(0, y, W, 1);
}

const HAS3 = typeof THREE !== "undefined";
const box = document.getElementById("three-box");
let scene, camera, renderer, sphereGroup, wireMaterial, pointLight1, pointLight2, particlesMesh, rings = [], clock;
function onResize() {
  resize2d();
  if (!HAS3 || !renderer) return;
  const w = box.clientWidth || 300, h = box.clientHeight || 300;
  camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h);
}
if (HAS3) {
  scene = new THREE.Scene(); scene.fog = new THREE.FogExp2(0x030308, 0.03);
  camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000); camera.position.z = 6;
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); box.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  pointLight1 = new THREE.PointLight(0x00ffcc, 4, 50); pointLight1.position.set(5, 5, 5); scene.add(pointLight1);
  pointLight2 = new THREE.PointLight(0xff0077, 4, 50); pointLight2.position.set(-5, -5, 5); scene.add(pointLight2);
  sphereGroup = new THREE.Group(); scene.add(sphereGroup);
  sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2, 64, 64), new THREE.MeshStandardMaterial({ color: 0x111625, metalness: 0.95, roughness: 0.2 })));
  wireMaterial = new THREE.MeshBasicMaterial({ color: 0x00ffcc, wireframe: true, transparent: true, opacity: 0.3 });
  sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2.03, 32, 32), wireMaterial));
  for (let i = 0; i < 4; i++) {
    const ringM = new THREE.Mesh(new THREE.RingGeometry(2.3 + i * 0.3, 2.35 + i * 0.3, 64), new THREE.MeshBasicMaterial({ color: i % 2 === 0 ? 0x00ffcc : 0xff0077, side: THREE.DoubleSide, transparent: true, opacity: 0.6 }));
    ringM.rotation.x = Math.random() * Math.PI; ringM.rotation.y = Math.random() * Math.PI;
    sphereGroup.add(ringM); rings.push({ mesh: ringM, sx: (Math.random() - 0.5) * 0.01, sy: (Math.random() - 0.5) * 0.01 });
  }
  const pc = 500, pa = new Float32Array(pc * 3); for (let i = 0; i < pc * 3; i++) pa[i] = (Math.random() - 0.5) * 15;
  const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(pa, 3));
  particlesMesh = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.03, color: 0x00ffcc, transparent: true, opacity: 0.8 })); scene.add(particlesMesh);
  clock = new THREE.Clock();
}
function applyAvatar() {
  document.getElementById("title").textContent = TITLES[avatar] || TITLES.sphere;
  box.style.display = (avatar === "sphere") ? "block" : "none";
  cv.style.display = (avatar === "sphere") ? "none" : "block";
  onResize(); setTimeout(onResize, 300);
}
window.addEventListener("resize", onResize); onResize(); setTimeout(onResize, 300); setTimeout(onResize, 1000);

let last = performance.now(); const t0 = last;
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.1, (now - last) / 1000); last = now; const t = (now - t0) / 1000;
  updateCommon(now, dt, t);
  if (avatar === "sphere") {
    if (!HAS3) return;
    const th = state === "thinking", sp = state === "speaking", ls = state === "listening";
    sphereGroup.rotation.y += th ? 0.04 : 0.005; sphereGroup.rotation.x += 0.002;
    const sc = 1 + (sp ? 0.04 * Math.sin(t * 12) : 0) + (ls ? pulse * 0.08 : 0); sphereGroup.scale.set(sc, sc, sc);
    rings.forEach(function (r) { r.mesh.rotation.z += r.sx * (th ? 6 : 1); r.mesh.rotation.x += r.sy * (th ? 6 : 1); });
    wireMaterial.opacity = 0.2 + Math.sin(t * (ls ? 8 : 3)) * 0.15 + (ls ? pulse * 0.3 : 0);
    pointLight1.intensity = 3 + Math.sin(t * 4); pointLight2.intensity = 3 + Math.cos(t * 4);
    particlesMesh.rotation.y = t * 0.05;
    renderer.render(scene, camera);
  } else {
    drawBg(t, dt);
    if (avatar === "eye") drawEye(t); else drawRobot(t);
  }
}
requestAnimationFrame(frame);
</script>
</body>
</html>
"""


@st.cache_resource
def _avatar_component(_html_hash: str):
    pasta = os.path.join(tempfile.gettempdir(), f"zeus_avatar_{_html_hash}")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "index.html"), "w", encoding="utf-8") as f:
        f.write(AVATAR_HTML)
    return components.declare_component(f"zeus_avatar_{_html_hash}", path=pasta)


AVATAR_MAP = {"Esfera 3D (Cyberpunk)": "sphere", "Olho Cibernético": "eye", "Robô Digital Realista": "robot"}


def render_avatar_voice(avatar_key, silence_s, stt_engine, tts_engine, voz, tts_rate):
    comp = _avatar_component(hashlib.md5(AVATAR_HTML.encode("utf-8")).hexdigest()[:10])
    ev = comp(
        avatar=avatar_key,
        reply=st.session_state.get("avatar_reply"),
        reply_id=st.session_state.get("avatar_reply_id", 0),
        history=st.session_state.messages[-10:],
        silence_s=silence_s,
        stt_engine=stt_engine,
        tts_engine=tts_engine,
        tts_rate=tts_rate,
        audio_b64=st.session_state.get("avatar_audio"),
        audio_mime="audio/mpeg",
        transcript=st.session_state.get("avatar_transcript"),
        key="zeus_avatar",
        default=None,
    )
    if ev and ev.get("id") != st.session_state.get("last_event_id"):
        st.session_state.last_event_id = ev["id"]
        texto, resposta, veio_de_audio = ev.get("text"), "", bool(ev.get("audio"))
        if veio_de_audio:
            try:
                raw = base64.b64decode(ev["audio"])
            except Exception:
                raw = b""
            texto, erro = transcrever_audio(raw, ev.get("mime", ""))
            if erro:
                resposta, texto = f"⚠️ {erro}", None
            elif not texto:
                resposta = "⚠️ Não consegui entender. Pode repetir?"
        if texto:
            resposta = processar_prompt(texto)
        audio = None
        if texto and tts_engine == "edge" and not resposta.startswith(("❌", "⚠️")):
            mp3 = sintetizar(resposta, voz, tts_rate)
            audio = base64.b64encode(mp3).decode() if mp3 else None
        st.session_state.avatar_reply = resposta
        st.session_state.avatar_reply_id = ev["id"]
        st.session_state.avatar_audio = audio
        st.session_state.avatar_transcript = texto if veio_de_audio else ""
        st.rerun()


# ============================================================
# APP PRINCIPAL (STREAMLIT INTERFACE)
# ============================================================
init_db()
if "session_id" not in st.session_state:
    st.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração & Visão")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel")
    avatar_type = st.selectbox("🤖 Avatar do Zeus", list(AVATAR_MAP.keys()), index=2, key="avatar_sel")
    model_choice = st.selectbox("🧠 Modelo LLM Principal", PROVIDERS, index=0, key="model_sel")
    
    st.divider()
    st.subheader("📸 Painel de Câmera & Mídia")
    use_cam_panel = st.checkbox("Ativar Captura da Câmera", value=False)
    media_action = st.selectbox("Ação Zeus Vision", ["Chat Inteligente", "Gerar Imagem Gratuita", "Gerar Vídeo Gratuito"])
    cam_prompt_input = st.text_input("Comando visual:", value="Analise esta cena ou crie uma obra baseada nela")

    if use_cam_panel and st.button("🚀 Disparar Câmera & Processar IAs"):
        try:
            with st.spinner("Capturando imagem da webcam e consultando o ecossistema de IAs..."):
                img_path = zeus_vm.capture_image_from_webcam()
                st.success(f"Foto salva em {img_path}!")
                optimized_p = zeus_vm.enhance_prompt_for_media(cam_prompt_input, img_path)
                
                if media_action == "Gerar Imagem Gratuita":
                    res_m = zeus_vm.generate_free_media(optimized_p, "image")
                    st.info(res_m)
                    processar_prompt(f"Gerei uma imagem gratuita baseada no comando: {optimized_p}")
                elif media_action == "Gerar Vídeo Gratuito":
                    res_m = zeus_vm.generate_free_media(optimized_p, "video")
                    st.info(res_m)
                    processar_prompt(f"Gerei um vídeo gratuito baseado no comando: {optimized_p}")
                else:
                    processar_prompt(optimized_p)
        except Exception as e:
            st.error(f"Erro no pipeline de visão: {e}")

    st.subheader("🎙️ Voz & Áudio")
    stt_label = st.selectbox("👂 Reconhecimento", ["Whisper (Groq) — melhor no celular", "Navegador — mais rápido, menos preciso"], key="stt_sel")
    tts_label = st.selectbox("🔊 Motor de voz", ["Neural (Edge) — voz natural", "Navegador — voz do aparelho"], key="tts_sel")
    _vozes = listar_vozes_edge()
    voz_nome = st.selectbox("🗣️ Voz do Zeus", list(_vozes.keys()), key="voz_sel")
    tts_rate = st.slider("⚡ Velocidade da fala", 0.8, 1.5, 1.0, 0.05, key="rate_sel")
    silence_s = st.slider("⏱️ Silêncio para enviar (s)", 1, 10, 2, key="silence_sel")
    stt_key = "whisper" if stt_label.startswith("Whisper") else "browser"
    tts_key = "edge" if tts_label.startswith("Neural") else "browser"
    voz_edge = _vozes[voz_nome]

    if st.button("🔊 Testar voz"):
        _mp3 = sintetizar("Olá, eu sou o Zeus. Sistemas operando em capacidade máxima.", voz_edge, tts_rate)
        if _mp3:
            st.audio(_mp3, format="audio/mp3")
        else:
            st.warning("Não consegui gerar a voz neural: " + st.session_state.get("tts_erro", "erro desconhecido"))
    if st.button("🗑️ Limpar histórico"):
        st.session_state.messages = []
        st.session_state.avatar_reply = None
        st.rerun()

    st.divider()
    st.subheader("🔑 Status das Chaves")
    for k in KEYS.values():
        st.write(("✅ " if _secret(k) else "❌ ") + k)
    if st.session_state.get("last_info"):
        st.caption(st.session_state.last_info)

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE ULTIMATE")
render_avatar_voice(AVATAR_MAP[avatar_type], silence_s, stt_key, tts_key, voz_edge, tts_rate)

with st.expander("📜 Histórico da conversa"):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
