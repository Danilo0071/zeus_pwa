import hashlib
import os
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
# CLIENTE LLM (GROQ, GEMINI, CLAUDE) COM RETENTATIVA E FALLBACK
# - 503/500 (alta demanda): tenta de novo e depois o próximo modelo
# - Se o provedor escolhido falhar (ex.: Claude sem créditos), usa
#   automaticamente outro provedor que tenha chave.
# Para forçar um modelo: Secrets GROQ_MODEL, GEMINI_MODEL, ANTHROPIC_MODEL.
# ============================================================
SYS_PROMPT = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico. Respostas curtas, pois serão faladas em voz alta."

PROVIDERS = ["groq", "gemini", "claude"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL"}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-2.5-flash"],
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5"],
}
TRANSIENT = (500, 502, 503, 504, 529)


def _normalize(msgs):
    """Garante papéis alternados começando por 'user'."""
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
                time.sleep(1.5)  # alta demanda: tenta de novo
                continue
            break
        if "credit balance" in last.lower() or code in (401, 403, 429):
            break
        if code in (400, 404) + TRANSIENT:
            continue  # próximo modelo
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


CALLERS = {"groq": _call_groq, "gemini": _call_gemini, "claude": _call_claude}


def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return text[:120]


def get_llm_response(session_id, prompt, model_choice):
    """Retorna (ok, texto, provedor_usado, lista_de_erros)."""
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
    add_message(sid, "user", prompt)
    ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok:
        add_message(sid, "assistant", response)
        st.session_state.last_info = f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "")
    else:
        st.session_state.last_info = "❌ " + " | ".join(erros)
    return response


# ============================================================
# AVATAR CYBERPUNK (Esfera 3D / Olho / Robô) + VOZ
# Componente bidirecional carregado de uma pasta temporária (path=).
# Voz: clique em 🎙️ e fale; após N segundos de silêncio (padrão 5) o
# texto é enviado e o Zeus responde em voz alta. Funciona nos 3 avatares.
# ============================================================
AVATAR_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #030308; font-family: 'Courier New', Courier, monospace; color: #00ffff; }
  #three-box, #c2 { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
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
<div class="ui-overlay">
  <h1 id="title">[ CYBER-SPHERE v3.0 ]</h1>
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
// ================= Ponte com o Streamlit =================
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 700});

let avatar = "sphere", SILENCE = 5, initialized = false, lastReplyId = 0, pendingEl = null;
let state = "idle", pulse = 0;
let micOn = false, pausedForBot = false, buffer = "", silenceTimer = null, silenceDeadline = 0, thinkTimer = null;
const TITLES = { sphere: "[ CYBER-SPHERE v3.0 ]", eye: "[ CYBER-EYE v3.0 ]", robot: "[ CYBER-BOT v3.0 ]" };
const STATE_TXT = { idle: "ONLINE", listening: "OUVINDO", thinking: "PROCESSANDO", speaking: "FALANDO" };
const statusEl = document.getElementById("status"), input = document.getElementById("userInput"), micBtn = document.getElementById("micBtn");

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

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.avatar && a.avatar !== avatar) { avatar = a.avatar; applyAvatar(); }
  else if (!initialized) { applyAvatar(); }
  SILENCE = a.silence_s || 5;
  if (!initialized) {
    initialized = true;
    (a.history || []).forEach(function (m) { addMessage(m.content, m.role === "user" ? "user" : "ai"); });
    lastReplyId = a.reply_id || 0;
    return;
  }
  if (a.reply && a.reply_id !== lastReplyId) {
    lastReplyId = a.reply_id;
    clearTimeout(thinkTimer);
    if (pendingEl) { pendingEl.remove(); pendingEl = null; }
    addMessage(a.reply, "ai");
    if (/^(❌|⚠️)/.test(a.reply)) afterSpeak(); else speak(a.reply);
  }
});

// ================= Chat =================
function addMessage(text, sender) {
  const h = document.getElementById("chatHistory");
  const d = document.createElement("div");
  d.classList.add("message"); d.classList.add(sender === "user" ? "user-msg" : "ai-msg");
  d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight;
  return d;
}
function sendText(text) {
  text = (text || "").trim();
  if (!text) return;
  clearTimeout(silenceTimer); silenceDeadline = 0; buffer = ""; input.value = "";
  addMessage(text, "user");
  pendingEl = addMessage("⏳ processando...", "ai");
  setState("thinking");
  pausedForBot = true; try { rec && rec.stop(); } catch (e) {}
  clearTimeout(thinkTimer);
  thinkTimer = setTimeout(function () { if (state === "thinking") afterSpeak(); }, 60000);
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: text }, dataType: "json" });
}
document.getElementById("sendBtn").addEventListener("click", function () { sendText(input.value); });
input.addEventListener("keydown", function (e) { if (e.key === "Enter") sendText(input.value); });

// ================= Fala (TTS) =================
let ptVoice = null;
function pickVoice() {
  const v = window.speechSynthesis.getVoices();
  ptVoice = v.find(function (x) { return /pt[-_]BR/i.test(x.lang); }) || v.find(function (x) { return /^pt/i.test(x.lang); }) || null;
}
if ("speechSynthesis" in window) { pickVoice(); window.speechSynthesis.onvoiceschanged = pickVoice; }
function speak(text) {
  if (!("speechSynthesis" in window)) { afterSpeak(); return; }
  setState("speaking");
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/[*#`_>]/g, ""));
  u.lang = "pt-BR"; u.pitch = 0.9; if (ptVoice) u.voice = ptVoice;
  u.onend = afterSpeak; u.onerror = afterSpeak;
  window.speechSynthesis.speak(u);
}
function afterSpeak() {
  setState("idle"); pausedForBot = false;
  if (micOn) setTimeout(function () { try { rec.start(); } catch (e) {} }, 400);
}

// ================= Voz contínua (envia após N s de silêncio) =================
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
function armSilence() {
  clearTimeout(silenceTimer);
  silenceDeadline = Date.now() + SILENCE * 1000;
  silenceTimer = setTimeout(function () { sendText(input.value); }, SILENCE * 1000);
}
if (SR) {
  rec = new SR(); rec.lang = "pt-BR"; rec.continuous = true; rec.interimResults = true;
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
  rec.onend = function () { if (micOn && !pausedForBot) setTimeout(function () { try { rec.start(); } catch (e) {} }, 300); };
  rec.onerror = function (e) {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") { micOn = false; micBtn.classList.remove("on"); statusEl.textContent = "STATUS: MICROFONE BLOQUEADO"; }
  };
  micBtn.addEventListener("click", function () {
    micOn = !micOn; micBtn.classList.toggle("on", micOn);
    if (micOn) { pausedForBot = false; buffer = ""; try { rec.start(); } catch (e) {} }
    else { clearTimeout(silenceTimer); silenceDeadline = 0; try { rec.stop(); } catch (e) {} if (state === "listening") setState("idle"); }
    renderStatus();
  });
} else { micBtn.disabled = true; micBtn.title = "Use Chrome ou Edge para voz"; }

// ================= Utilidades gráficas =================
const rnd = function (a, b) { return a + Math.random() * (b - a); };
const CY = "#00e5ff", MG = "#ff00ff";
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

// ================= Canvas 2D (olho e robô) =================
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
  ctx.strokeStyle = "rgba(255,0,255,0.18)"; ctx.lineWidth = 1;
  for (let i = -12; i <= 12; i++) { ctx.beginPath(); ctx.moveTo(W / 2 + i * 16, hy); ctx.lineTo(W / 2 + i * W / 9, H); ctx.stroke(); }
  for (let j = 0; j < 8; j++) { const p = (j + (t * 0.4) % 1) / 8; const y = hy + (H - hy) * p * p; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  parts.forEach(function (p) {
    p.y -= p.v * dt; if (p.y < 0) { p.y = 1; p.x = Math.random(); }
    ctx.fillStyle = "rgba(0,229,255,0.6)"; ctx.fillRect(p.x * W, p.y * H, p.s, p.s);
  });
}

function drawEye(t) {
  const cx = W / 2, cy = H * 0.36, R = Math.min(W * 0.26, H * 0.17), rad = Math.PI / 180;
  const ls = state === "listening", al = (60 + (ls ? 100 : 35) * (0.5 + 0.5 * Math.sin(t * (ls ? 6 : 2)))) / 255;
  let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.9); g.addColorStop(0, "rgba(0,229,255," + al + ")"); g.addColorStop(1, "rgba(0,229,255,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 1.9, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(0,229,255,0.35)"; ctx.lineWidth = 1;
  for (let i = 0; i < 60; i++) { const a = (i * 6 - ring * 0.3) * rad, r1 = R * 1.62, r2 = R * (i % 5 ? 1.67 : 1.72); ctx.beginPath(); ctx.moveTo(cx + r1 * Math.cos(a), cy + r1 * Math.sin(a)); ctx.lineTo(cx + r2 * Math.cos(a), cy + r2 * Math.sin(a)); ctx.stroke(); }
  ctx.lineWidth = 2; ctx.strokeStyle = "#1e90ff"; arcs(cx, cy, R * 1.5, -ring * rad, 3, 50 * rad);
  ctx.strokeStyle = MG; arcs(cx, cy, R * 1.82, ring * 0.6 * rad, 2, 35 * rad);
  const oh = Math.max(0.02, open);
  ctx.save(); ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.45, R * 0.85 * oh, 0, 0, 7); ctx.clip();
  ctx.fillStyle = "#060e1c"; ctx.fillRect(0, 0, W, H);
  const ix = cx + look.x * R * 0.5, iy = cy + look.y * R * 0.28, ir = R * 0.72;
  g = ctx.createRadialGradient(ix, iy, 0, ix, iy, ir); g.addColorStop(0, "#001e46"); g.addColorStop(0.6, "#006ebe"); g.addColorStop(1, CY);
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.fill();
  const pr = ir * 0.38 * pupil;
  ctx.strokeStyle = "rgba(0,229,255,0.3)"; ctx.lineWidth = 1;
  for (let i = 0; i < 36; i++) { const a = i * 10 * rad; ctx.beginPath(); ctx.moveTo(ix + pr * Math.cos(a), iy + pr * Math.sin(a)); ctx.lineTo(ix + ir * 0.95 * Math.cos(a), iy + ir * 0.95 * Math.sin(a)); ctx.stroke(); }
  ctx.strokeStyle = "#78f0ff"; ctx.lineWidth = 2; arcs(ix, iy, ir * 0.88, ring * 1.5 * rad, 4, 40 * rad);
  ctx.fillStyle = "#000"; ctx.strokeStyle = CY; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(ix, iy, pr, 0, 7); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.beginPath(); ctx.arc(ix - pr * 0.4, iy - pr * 0.4, pr * 0.18, 0, 7); ctx.fill();
  ctx.restore();
  glow(CY, 14); ctx.strokeStyle = CY; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.45, R * 0.85 * oh, 0, 0, 7); ctx.stroke(); noGlow();
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

function drawRobot(t) {
  const cx = W / 2, cy = H * 0.38, S = Math.min(W * 0.5, H * 0.46), hw = S * 0.5, hh = S * 0.42;
  const ls = state === "listening", th = state === "thinking", sp = state === "speaking";
  let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, S * 0.95); g.addColorStop(0, "rgba(0,229,255," + (0.18 + (ls ? 0.12 * Math.sin(t * 6) : 0)) + ")"); g.addColorStop(1, "rgba(0,229,255,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, S * 0.95, 0, 7); ctx.fill();
  // antena
  ctx.strokeStyle = CY; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(cx, cy - hh); ctx.lineTo(cx, cy - hh - S * 0.14); ctx.stroke();
  const ar = S * 0.035 * (1 + 0.4 * Math.sin(t * (th ? 12 : 4)));
  glow(MG, 18); ctx.fillStyle = MG; ctx.beginPath(); ctx.arc(cx, cy - hh - S * 0.14, ar, 0, 7); ctx.fill(); noGlow();
  if (th) { ctx.strokeStyle = MG; ctx.lineWidth = 2; arcs(cx, cy - hh - S * 0.14, S * 0.07, ring * Math.PI / 180 * 2, 3, 1.0); }
  // orelhas
  ctx.fillStyle = "#1a0b2e"; ctx.strokeStyle = MG; ctx.lineWidth = 2;
  rr(cx - hw - S * 0.07, cy - S * 0.1, S * 0.07, S * 0.2, 6); ctx.fill(); ctx.stroke();
  rr(cx + hw, cy - S * 0.1, S * 0.07, S * 0.2, 6); ctx.fill(); ctx.stroke();
  // cabeça
  g = ctx.createLinearGradient(0, cy - hh, 0, cy + hh); g.addColorStop(0, "#1a2240"); g.addColorStop(1, "#080b18");
  glow(CY, 18); ctx.fillStyle = g; ctx.strokeStyle = CY; ctx.lineWidth = 3; rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.1); ctx.fill(); ctx.stroke(); noGlow();
  // detalhes de circuito
  ctx.strokeStyle = "rgba(255,0,255,0.4)"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx - hw * 0.9, cy + hh * 0.15); ctx.lineTo(cx - hw * 0.6, cy + hh * 0.15); ctx.lineTo(cx - hw * 0.5, cy + hh * 0.3); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx + hw * 0.9, cy + hh * 0.15); ctx.lineTo(cx + hw * 0.6, cy + hh * 0.15); ctx.lineTo(cx + hw * 0.5, cy + hh * 0.3); ctx.stroke();
  // visor
  ctx.fillStyle = "#02030a"; ctx.strokeStyle = "rgba(0,229,255,0.5)"; ctx.lineWidth = 2;
  rr(cx - hw * 0.82, cy - hh * 0.64, hw * 1.64, hh * 0.56, 14); ctx.fill(); ctx.stroke();
  const ey = cy - hh * 0.36, lx = look.x * hw * 0.08, ly = look.y * hh * 0.05;
  if (th) {
    const bx = cx + Math.sin(t * 3) * hw * 0.62;
    glow(CY, 22); ctx.fillStyle = CY; rr(bx - hw * 0.14, ey - hh * 0.09, hw * 0.28, hh * 0.18, 6); ctx.fill(); noGlow();
  } else {
    [-1, 1].forEach(function (s) {
      const rx = hw * 0.22 * (ls ? 1 + pulse * 0.25 : 1), ry = Math.max(1, hh * 0.17 * open);
      glow(ls ? MG : CY, 22); ctx.fillStyle = ls ? MG : CY;
      ctx.beginPath(); ctx.ellipse(cx + s * hw * 0.42 + lx, ey + ly, rx, ry, 0, 0, 7); ctx.fill(); noGlow();
      ctx.fillStyle = "rgba(255,255,255,0.85)"; ctx.beginPath(); ctx.arc(cx + s * hw * 0.42 + lx - rx * 0.3, ey + ly - ry * 0.3, Math.max(1, ry * 0.2), 0, 7); ctx.fill();
    });
  }
  // boca (equalizador)
  const my = cy + hh * 0.52, bars = 11, bw = hw * 0.12, gap = hw * 0.06, total = bars * bw + (bars - 1) * gap, x0 = cx - total / 2;
  glow(ls ? MG : CY, 12); ctx.fillStyle = ls ? MG : CY;
  for (let i = 0; i < bars; i++) {
    let h = hh * 0.03;
    if (sp) h = hh * 0.06 + hh * 0.16 * Math.abs(Math.sin(t * 10 + i * 1.3) * Math.cos(t * 4 + i));
    else if (ls) h = hh * 0.03 + hh * 0.12 * pulse * Math.abs(Math.sin(t * 8 + i));
    ctx.fillRect(x0 + i * (bw + gap), my - h, bw, h * 2);
  }
  noGlow();
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

// ================= Three.js (esfera) =================
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
  scene.add(new THREE.AmbientLight(0xffffff, 0.5));
  pointLight1 = new THREE.PointLight(0x00ffff, 3, 50); pointLight1.position.set(5, 5, 5); scene.add(pointLight1);
  pointLight2 = new THREE.PointLight(0xff00ff, 3, 50); pointLight2.position.set(-5, -5, 5); scene.add(pointLight2);
  sphereGroup = new THREE.Group(); scene.add(sphereGroup);
  sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2, 64, 64), new THREE.MeshStandardMaterial({ color: 0x111625, metalness: 0.95, roughness: 0.2 })));
  wireMaterial = new THREE.MeshBasicMaterial({ color: 0x00ffff, wireframe: true, transparent: true, opacity: 0.25 });
  sphereGroup.add(new THREE.Mesh(new THREE.SphereGeometry(2.03, 32, 32), wireMaterial));
  for (let i = 0; i < 4; i++) {
    const ringM = new THREE.Mesh(new THREE.RingGeometry(2.3 + i * 0.3, 2.35 + i * 0.3, 64), new THREE.MeshBasicMaterial({ color: i % 2 === 0 ? 0x00ffff : 0xff00ff, side: THREE.DoubleSide, transparent: true, opacity: 0.6 }));
    ringM.rotation.x = Math.random() * Math.PI; ringM.rotation.y = Math.random() * Math.PI;
    sphereGroup.add(ringM); rings.push({ mesh: ringM, sx: (Math.random() - 0.5) * 0.01, sy: (Math.random() - 0.5) * 0.01 });
  }
  const pc = 500, pa = new Float32Array(pc * 3); for (let i = 0; i < pc * 3; i++) pa[i] = (Math.random() - 0.5) * 15;
  const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(pa, 3));
  particlesMesh = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.03, color: 0x00ffff, transparent: true, opacity: 0.8 })); scene.add(particlesMesh);
  clock = new THREE.Clock();
}
function applyAvatar() {
  document.getElementById("title").textContent = TITLES[avatar] || TITLES.sphere;
  box.style.display = (avatar === "sphere") ? "block" : "none";
  cv.style.display = (avatar === "sphere") ? "none" : "block";
  onResize(); setTimeout(onResize, 300);
}
window.addEventListener("resize", onResize); onResize(); setTimeout(onResize, 300); setTimeout(onResize, 1000);

// ================= Loop principal =================
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
    pointLight1.intensity = 2 + Math.sin(t * 4); pointLight2.intensity = 2 + Math.cos(t * 4);
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


AVATAR_MAP = {"Esfera 3D (Cyberpunk)": "sphere", "Olho Cibernético": "eye", "Robô Digital": "robot"}


def render_avatar_voice(avatar_key: str, silence_s: int):
    comp = _avatar_component(hashlib.md5(AVATAR_HTML.encode("utf-8")).hexdigest()[:10])
    ev = comp(
        avatar=avatar_key,
        reply=st.session_state.get("avatar_reply"),
        reply_id=st.session_state.get("avatar_reply_id", 0),
        history=st.session_state.messages[-10:],
        silence_s=silence_s,
        key="zeus_avatar",
        default=None,
    )
    if ev and ev.get("id") != st.session_state.get("last_event_id"):
        st.session_state.last_event_id = ev["id"]
        resposta = processar_prompt(ev["text"])
        st.session_state.avatar_reply = resposta
        st.session_state.avatar_reply_id = ev["id"]
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
    avatar_type = st.selectbox("🤖 Avatar do Zeus", list(AVATAR_MAP.keys()), index=0, key="avatar_sel")
    model_choice = st.selectbox("🧠 Modelo LLM", PROVIDERS, index=0, key="model_sel")
    silence_s = st.slider("⏱️ Segundos de silêncio para enviar a fala", 2, 10, 5, key="silence_sel")
    if st.button("🗑️ Limpar conversa"):
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
st.title("⚡ ZEUS // CYBER-CORE")
render_avatar_voice(AVATAR_MAP[avatar_type], silence_s)

with st.expander("📜 Histórico da conversa"):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
