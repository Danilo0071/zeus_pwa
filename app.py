# ============================================================
# ZEUS 4.5 - APLICATIVO PRINCIPAL CONSOLIDADO E ENTERPRISE
# (VERSÃO CORRIGIDA E OTIMIZADA)
# ============================================================
import streamlit as st
import streamlit.components.v1 as components
import os
import sys
import time
import json
import base64
import sqlite3
import hashlib
import asyncio
import tempfile
import requests
import re

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Zeus 4.5 // Cyber-Core Enterprise",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

THEMES = {
    "Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"},
    "Matrix Neon": {"bg": "#050a05", "panel": "#0a140a", "primary": "#00ff66", "secondary": "#008833", "text": "#00ff66", "border": "#00ff6633"},
    "Deep Space": {"bg": "#090a10", "panel": "#10121d", "primary": "#9d4edd", "secondary": "#3f37c9", "text": "#e0aaff", "border": "#9d4edd33"}
}

THEME_CSS = """
<style>
    .stApp { background-color: _BG; color: __TEXT_; font-family: 'Courier New', monospace; }
    [data-testid="stSidebar"] { background-color: _PANEL; border-right: 1px solid __BORDER_; }
    h1, h2, h3 { color: _PRIMARY_ !important; text-shadow: 0 0 12px _BORDER_; }
    .stButton>button { background: linear-gradient(135deg, _PRIMARY, __SECONDARY); color: #030308; font-weight: bold; border: none; border-radius: 6px; box-shadow: 0 0 10px __BORDER_; transition: 0.3s; }
    .stButton>button:hover { opacity: 0.85; transform: scale(1.02); }
    .stTextInput>div>div>input, .stSelectbox>div>div>div { background-color: _PANEL_ !important; color: _TEXT_ !important; border: 1px solid _BORDER_ !important; }
</style>
"""

def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    css = THEME_CSS
    for token, chave in (("_BG", "bg"), ("TEXT", "text"), ("PANEL", "panel"), ("PRIMARY", "primary"), ("BORDER", "border"), ("SECONDARY_", "secondary")):
        css = css.replace(token, t[chave])
    st.markdown(css, unsafe_allow_html=True)

# ============================================================
# BANCO DE DADOS LOCAL (HISTÓRICO PERSISTENTE)
# ============================================================
DB_FILE = "zeus_chat.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
        conn.close()
    except Exception:
        pass

def add_message(session_id, role, content):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_recent_context(session_id, limit=8):
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []

# ============================================================
# GERENCIAMENTO SEGURO DE CHAVES E SECRETS
# ============================================================
def _limpar_chave(v: str) -> str:
    if not v:
        return ""
    v = re.sub(r"\s+", "", str(v)).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            val = st.secrets.get(key)
            if val:
                cleaned = _limpar_chave(str(val))
                if cleaned:
                    return cleaned
    except Exception:
        pass
    env_val = os.environ.get(key)
    if env_val:
        cleaned = _limpar_chave(env_val)
        if cleaned:
            return cleaned
    return _limpar_chave(default)

# ============================================================
# CLIENTE LLM MULTIPROVEDOR COM FALLBACK
# ============================================================
SYS_PROMPT = "Você é Zeus 4.5, uma IA cyberpunk futurista, assistente sênior e autônoma. Responda em português, com precisão técnica, objetividade e tom futurista."

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]

KEYS = {
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"
}

MODEL_SECRET = {
    "groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL",
    "huggingface": "HF_MODEL", "github": "GITHUB_MODEL",
    "deepinfra": "DEEPINFRA_MODEL", "openrouter": "OPENROUTER_MODEL"
}

MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-3.5-flash", "gemini-pro"],
    "claude": ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["cognitivecomputations/dolphin-2.9-llama3-8b", "meta-llama/Llama-3.3-70B-Instruct"],
    "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"]
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
            if code == 200: return True, text
            last = f"({code}) {text[:200]}"
            if code in TRANSIENT and attempt == 0:
                time.sleep(1.0)
                continue
            break
        if "credit balance" in last.lower() or code in (401, 403, 429): break
        if code in (400, 404) + TRANSIENT: continue
        break
    return False, last

def _call_groq(api_key, model, msgs, system=None):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1000}, timeout=60)
    if r.status_code == 200: return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text

def _call_claude(api_key, model, msgs, system=None):
    r = requests.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs}, timeout=60)
    if r.status_code == 200: return 200, "".join(b.get("text", "") for b in r.json()["content"])
    return r.status_code, r.text

def _call_gemini(api_key, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents}, timeout=60)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text

def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers: headers.update(extra_headers)
    r = requests.post(url, headers=headers, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1000}, timeout=60)
    if r.status_code == 200: return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text

def _call_huggingface(api_key, model, msgs, system=None):
    return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", api_key, model, msgs, system)

def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat("https://models.github.ai/inference/chat/completions", api_key, model, msgs, system, {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=api_key, base_url=base, timeout=60)
            resp = client.chat.completions.create(model=model, temperature=0.4, max_tokens=1000, messages=[{"role": "system", "content": system or SYS_PROMPT}] + msgs)
            return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)

def _call_openrouter(api_key, model, msgs, system=None):
    return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", api_key, model, msgs, system, {"X-Title": "Zeus 4.5"})

def _call_gemini_auto(api_key, model, msgs, system=None):
    code, texto = _call_gemini(api_key, model, msgs, system, "v1")
    if code in (400, 404):
        code2, texto2 = _call_gemini(api_key, model, msgs, system, "v1beta")
        if code2 == 200: return code2, texto2
    return code, texto

CALLERS = {
    "groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude,
    "huggingface": _call_huggingface, "github": _call_github,
    "deepinfra": _call_deepinfra, "openrouter": _call_openrouter,
}

def _resumo(text):
    low = text.lower()
    if "credit balance" in low: return "sem créditos"
    if "high demand" in low or "unavailable" in low: return "alta demanda"
    return text[:120]

def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}])
    order = [model_choice] + [p for p in PROVIDERS if p != model_choice]
    erros = []
    for prov in order:
        api_key = _secret(KEYS[prov])
        if not api_key: continue
        override = _secret(MODEL_SECRET[prov])
        candidates = [override] if override else MODELS[prov]
        try:
            ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs, system))
        except Exception as e:
            ok, text = False, f"erro: {e}"
        if ok: return True, text or "(vazio)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

def processar_prompt(prompt):
    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok: add_message(sid, "assistant", response)
    return response

# ============================================================
# MÓDULO DE VOZ E ÁUDIO
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    api_key = _secret("GROQ_API_KEY")
    if not api_key: return "", "GROQ_API_KEY ausente."
    try:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {api_key}"}, files={"file": ("audio.webm", audio_bytes, mime or "audio/webm")}, data={"model": "whisper-large-v3-turbo", "language": "pt"}, timeout=60)
        if r.status_code != 200: return "", f"Erro: {r.text[:100]}"
        return r.json().get("text", "").strip(), ""
    except Exception as e:
        return "", str(e)

async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
        if c["type"] == "audio":
            chunks.append(c["data"])
    return b"".join(chunks)

def sintetizar(texto, voz, rate=1.0):
    try:
        pct = f"{int(round((rate - 1) * 100)):+d}%"
        # Correção para evitar conflito de loop no Streamlit
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(asyncio.wait_for(_edge_synth(texto, voz, pct), timeout=25))
        loop.close()
        return res
    except Exception:
        return None

@st.cache_data(ttl=86400, show_spinner=False)
def listar_vozes_edge():
    try:
        import edge_tts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        todas = loop.run_until_complete(edge_tts.list_voices())
        loop.close()
        
        pt = [v for v in todas if v["Locale"].lower().startswith("pt-")]
        vozes = {}
        for v in pt:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
            vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes or {"Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural", "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural"}
    except Exception:
        return {"Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural"}

# ============================================================
# MÍDIA (IMAGENS E VÍDEOS)
# ============================================================
def make_image(prompt, log):
    key = _secret("DEEPINFRA_API_KEY")
    if key:
        try:
            r = requests.post("https://api.deepinfra.com/v1/openai/images/generations", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"prompt": prompt, "size": "1024x1024", "model": "black-forest-labs/FLUX-1-schnell"}, timeout=120)
            if r.status_code == 200:
                log.append("✅ Imagem gerada via DeepInfra")
                return base64.b64decode(r.json()["data"][0]["b64_json"])
        except Exception as e:
            log.append(f"Erro DeepInfra Imagem: {e}")
    try:
        from urllib.parse import quote
        r = requests.get(f"https://image.pollinations.ai/prompt/{quote(prompt)}", timeout=60)
        if r.status_code == 200:
            log.append("✅ Imagem gerada via Pollinations")
            return r.content
    except Exception:
        pass
    return None

def make_video(prompt, log):
    if not _secret("HUGGINGFACE_API_KEY"):
        log.append("⚪ Vídeo: Sem HUGGINGFACE_API_KEY")
        return None
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=300)
        out = client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-1.3B")
        log.append("✅ Vídeo gerado via Hugging Face")
        return out if isinstance(out, bytes) else out.read() if hasattr(out, "read") else None
    except Exception as e:
        log.append(f"❌ Erro Vídeo: {e}")
        return None

# ============================================================
# NÚCLEO DO GITHUB E SPOTIFY (CORRIGIDO)
# ============================================================
def _obter_token_github():
    return _secret("GITHUB_TOKEN") or _secret("GITHUB_PAT") or _secret("GITHUB_API_KEY")

def zeus_salvar_codigo_github(caminho_arquivo: str, codigo_conteudo: str, mensagem: str = "Zeus 4.5 Auto-Update"):
    token = _obter_token_github()
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo):
        return {"status": "erro", "mensagem": "❌ Faltam credenciais do GitHub nos Secrets."}

    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp_get = requests.get(url, headers=headers, timeout=25)
        sha = resp_get.json().get("sha") if resp_get.status_code == 200 else None
        payload = {"message": mensagem, "content": base64.b64encode(codigo_conteudo.encode("utf-8")).decode("utf-8")}
        if sha: payload["sha"] = sha
        resp_put = requests.put(url, headers=headers, json=payload, timeout=25)
        if resp_put.status_code in (200, 201):
            return {"status": "sucesso", "mensagem": f"✅ GitHub sincronizado em '{caminho_arquivo}'.", "url": resp_put.json().get("content", {}).get("html_url", "")}
        return {"status": "erro", "mensagem": f"❌ Erro GitHub HTTP {resp_put.status_code}: {resp_put.text[:150]}"}
    except Exception as e:
        return {"status": "erro", "mensagem": f"❌ Erro de conexão: {str(e)[:150]}"}

def zeus_listar_memoria_github(pasta: str = "nucleo_zeus/modulos"):
    token = _obter_token_github()
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo): return []
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{pasta}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return [item['path'] for item in resp.json() if item.get('type') == 'file']
    except Exception:
        pass
    return []

def zeus_ler_arquivo_github(caminho_arquivo: str):
    token = _obter_token_github()
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo): return None
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return base64.b64decode(resp.json()["content"]).decode("utf-8")
    except Exception:
        pass
    return None

def zeus_aprender_ensemble(instrucao: str, linguagem: str = "python") -> str:
    sys_gerar = f"Você é um engenheiro sênior em {linguagem}. Escreva código funcional, robusto, limpo e documentado. Responda APENAS com o código puro."
    ok, texto1, prov1, _ = get_llm_response(st.session_state.session_id, instrucao, "groq", system=sys_gerar, use_history=False)
    if not ok: return "❌ Falha ao gerar código via IA."
    
    codigo_v1 = re.sub(r"^[a-zA-Z]*\n", "", texto1)
    codigo_v1 = re.sub(r"\n$", "", codigo_v1).strip()

    slug = "".join(c if c.isalnum() else "" for c in instrucao[:25]).lower() or "modulo"
    caminho = f"nucleo_zeus/modulos/modulo_{slug}.py"
    
    res_save = zeus_salvar_codigo_github(caminho, codigo_v1, f"Zeus auto: {instrucao[:40]}")
    if res_save["status"] == "sucesso":
        return f"✅ Módulo gerado por *{prov1}* e salvo com sucesso no GitHub!\n[Ver no Repositório]({res_save['url']})"
    return f"❌ Falha ao salvar no GitHub: {res_save['mensagem']}"

def zeus_tocar_musica(nome_musica: str) -> str:
    if not spotipy: return "❌ Biblioteca 'spotipy' não instalada."
    try:
        client_id = st.secrets.get("SPOTIFY_CLIENT_ID")
        client_secret = st.secrets.get("SPOTIFY_CLIENT_SECRET")
        refresh_token = st.secrets.get("SPOTIFY_REFRESH_TOKEN")
        if not (client_id and client_secret and refresh_token):
            return "❌ Credenciais do Spotify ausentes nos Secrets."

        auth_manager = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri="https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/", scope="user-modify-playback-state user-read-playback-state", cache_path=None)
        token_info = auth_manager.refresh_access_token(refresh_token)
        sp = spotipy.Spotify(auth=token_info["access_token"])
        
        results = sp.search(q=nome_musica, limit=1, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        
        if not tracks:
            return f"❌ Nenhuma faixa encontrada para '{nome_musica}'."
            
        # Correção do índice da lista para tocar corretamente
        sp.start_playback(uris=[tracks[0]["uri"]])
        return f"✅ Tocando: {tracks[0]['name']} - {tracks[0]['artists'][0]['name']}"
    except Exception as e:
        return f"❌ Erro Spotify: {str(e)[:120]}"

# ============================================================
# AVATAR COMPONENT
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
  h1 { font-size: 1.4rem; margin: 0; text-shadow: 0 0 12px #00ffff; letter-spacing: 2px; }
  p { font-size: 0.85rem; color: #ff00ff; margin: 6px 0 0 0; }
  .chat-container { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); width: 94%; max-width: 650px; background: rgba(3,3,15,0.9); border: 1px solid #00ffff; border-radius: 8px; padding: 12px; z-index: 10; box-sizing: border-box; box-shadow: 0 0 20px rgba(0,255,255,0.2); }
  .chat-history { height: 110px; overflow-y: auto; font-size: 0.85rem; margin-bottom: 8px; display: flex; flex-direction: column; gap: 6px; }
  .message { padding: 6px 10px; border-radius: 4px; max-width: 85%; }
  .user-msg { background: rgba(255,0,255,0.15); border-left: 3px solid #ff00ff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(0,255,255,0.15); border-left: 3px solid #00ffff; color: #00ffff; align-self: flex-start; }
  .input-group { display: flex; gap: 6px; }
  input[type="text"] { flex-grow: 1; background: rgba(0,0,0,0.7); border: 1px solid #00ffff; color: #00ffff; padding: 8px; border-radius: 4px; outline: none; }
  button { background: linear-gradient(135deg, #00ffff, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="three-box"></div>
<canvas id="c2"></canvas>
<div class="ui-overlay">
  <h1 id="title">[ ZEUS 4.5 // CYBER-CORE ]</h1>
  <p id="status">STATUS: ONLINE & SECURE</p>
</div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <input type="text" id="userInput" placeholder="Comando neural..." />
    <button id="sendBtn">Transmitir</button>
  </div>
</div>
<script>
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 650});

let avatar = "sphere", lastReplyId = 0;
let mouseX = 0, mouseY = 0;
const box = document.getElementById("three-box"), cv = document.getElementById("c2"), ctx = cv.getContext("2d");

window.addEventListener("mousemove", (e) => {
  mouseX = (e.clientX / window.innerWidth) * 2 - 1;
  mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
});

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.avatar && a.avatar !== avatar) { avatar = a.avatar; updateAvatarMode(); }
  if (a.reply_id !== lastReplyId) {
    lastReplyId = a.reply_id;
    if (a.reply) addMessage(a.reply, "ai");
  }
});

function updateAvatarMode() {
  box.style.display = (avatar === "sphere" || avatar === "gods_lightning") ? "block" : "none";
  cv.style.display = (avatar === "sphere" || avatar === "gods_lightning") ? "none" : "block";
}

function addMessage(text, sender) {
  const h = document.getElementById("chatHistory");
  const d = document.createElement("div");
  d.classList.add("message", sender === "user" ? "user-msg" : "ai-msg");
  d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight;
}

document.getElementById("sendBtn").addEventListener("click", submitText);
document.getElementById("userInput").addEventListener("keydown", function(e) { if(e.key === "Enter") submitText(); });

function submitText() {
  const input = document.getElementById("userInput");
  const val = input.value.trim();
  if(!val) return;
  addMessage(val, "user");
  input.value = "";
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: val }, dataType: "json" });
}

const HAS3 = typeof THREE !== "undefined";
let scene, camera, renderer, sphere, lightningGroup;
if (HAS3) {
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000); camera.position.z = 6;
  
  # ============================================================
# CLIENTE LLM MULTIPROVEDOR COM FALLBACK
# ============================================================
SYS_PROMPT = "Você é Zeus 5.0, uma IA cyberpunk futurista, assistente sênior e autônoma. Responda em português, com precisão técnica, objetividade e tom futurista."

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]

KEYS = {
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"
}
MODEL_SECRET = {
    "groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL",
    "huggingface": "HF_MODEL", "github": "GITHUB_MODEL",
    "deepinfra": "DEEPINFRA_MODEL", "openrouter": "OPENROUTER_MODEL"
}

MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-3.5-flash", "gemini-pro"],
    "claude": ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["cognitivecomputations/dolphin-2.9-llama3-8b", "meta-llama/Llama-3.3-70B-Instruct"],
    "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"]
}

TRANSIENT = (500, 502, 503, 504, 529)

def _normalize(msgs):
    out = []
    for m in msgs:
        if m["role"] not in ("user", "assistant") or not str(m["content"]).strip(): continue
        if out and out[-1]["role"] == m["role"]: out[-1]["content"] += "\n" + m["content"]
        else: out.append({"role": m["role"], "content": m["content"]})
    while out and out[0]["role"] != "user": out.pop(0)
    return out

def _try_models(candidates, fn):
    last = ""
    for model in candidates:
        for attempt in range(2):
            code, text = fn(model)
            if code == 200: return True, text
            last = f"({code}) {text[:200]}"
            if code in TRANSIENT and attempt == 0:
                time.sleep(1.0)
                continue
            break
        if "credit balance" in last.lower() or code in (401, 403, 429): break
        if code in (400, 404) + TRANSIENT: continue
        break
    return False, last

def _call_groq(api_key, model, msgs, system=None):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1000}, timeout=60)
    return (200, (r.json()["choices"][0]["message"].get("content") or "").strip()) if r.status_code == 200 else (r.status_code, r.text)

def _call_claude(api_key, model, msgs, system=None):
    r = requests.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs}, timeout=60)
    return (200, "".join(b.get("text", "") for b in r.json()["content"])) if r.status_code == 200 else (r.status_code, r.text)

def _call_gemini(api_key, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents}, timeout=60)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text

def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers: headers.update(extra_headers)
    r = requests.post(url, headers=headers, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 1000}, timeout=60)
    return (200, (r.json()["choices"][0]["message"].get("content") or "").strip()) if r.status_code == 200 else (r.status_code, r.text)

def _call_huggingface(api_key, model, msgs, system=None): return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", api_key, model, msgs, system)
def _call_github(api_key, model, msgs, system=None): return _call_openai_compat("https://models.github.ai/inference/chat/completions", api_key, model, msgs, system, {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=api_key, base_url=base, timeout=60)
            resp = client.chat.completions.create(model=model, temperature=0.4, max_tokens=1000, messages=[{"role": "system", "content": system or SYS_PROMPT}] + msgs)
            return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e: return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)

def _call_openrouter(api_key, model, msgs, system=None): return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", api_key, model, msgs, system, {"X-Title": "Zeus 5.0"})
def _call_gemini_auto(api_key, model, msgs, system=None):
    code, texto = _call_gemini(api_key, model, msgs, system, "v1")
    if code in (400, 404):
        code2, texto2 = _call_gemini(api_key, model, msgs, system, "v1beta")
        if code2 == 200: return code2, texto2
    return code, texto

CALLERS = {"groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude, "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra, "openrouter": _call_openrouter}

def _resumo(text):
    low = text.lower()
    if "credit balance" in low: return "sem créditos"
    if "high demand" in low or "unavailable" in low: return "alta demanda"
    return text[:120]

def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}])
    order = [model_choice] + [p for p in PROVIDERS if p != model_choice]
    erros = []
    for prov in order:
        api_key = _secret(KEYS[prov])
        if not api_key: continue
        override = _secret(MODEL_SECRET[prov])
        candidates = [override] if override else MODELS[prov]
        try: ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs, system))
        except Exception as e: ok, text = False, f"erro: {e}"
        if ok: return True, text or "(vazio)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

def processar_prompt(prompt):
    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok: add_message(sid, "assistant", response)
    return response

# ============================================================
# MÓDULO DE VOZ E ÁUDIO
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    api_key = _secret("GROQ_API_KEY")
    if not api_key: return "", "GROQ_API_KEY ausente."
    try:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {api_key}"}, files={"file": ("audio.webm", audio_bytes, mime or "audio/webm")}, data={"model": "whisper-large-v3-turbo", "language": "pt"}, timeout=60)
        if r.status_code != 200: return "", f"Erro: {r.text[:100]}"
        return r.json().get("text", "").strip(), ""
    except Exception as e: return "", str(e)

async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
        if c["type"] == "audio": chunks.append(c["data"])
    return b"".join(chunks)

def sintetizar(texto, voz, rate=1.0):
    try:
        pct = f"{int(round((rate - 1) * 100)):+d}%"
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(asyncio.wait_for(_edge_synth(texto, voz, pct), timeout=25))
        loop.close()
        return res
    except Exception: return None

@st.cache_data(ttl=86400, show_spinner=False)
def listar_vozes_edge():
    try:
        import edge_tts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        todas = loop.run_until_complete(edge_tts.list_voices())
        loop.close()
        pt = [v for v in todas if v["Locale"].lower().startswith("pt-")]
        vozes = {}
        for v in pt:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
            vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes or {"Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural", "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural"}
    except Exception: return {"Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural"}
    
    # ============================================================
# MÍDIA (IMAGENS E VÍDEOS)
# ============================================================
def make_image(prompt, log):
    key = _secret("DEEPINFRA_API_KEY")
    if key:
        try:
            r = requests.post("https://api.deepinfra.com/v1/openai/images/generations", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"prompt": prompt, "size": "1024x1024", "model": "black-forest-labs/FLUX-1-schnell"}, timeout=120)
            if r.status_code == 200:
                log.append("✅ Imagem gerada via DeepInfra")
                return base64.b64decode(r.json()["data"][0]["b64_json"])
        except Exception as e: log.append(f"Erro DeepInfra Imagem: {e}")
    try:
        from urllib.parse import quote
        r = requests.get(f"https://image.pollinations.ai/prompt/{quote(prompt)}", timeout=60)
        if r.status_code == 200:
            log.append("✅ Imagem gerada via Pollinations")
            return r.content
    except Exception: pass
    return None

def make_video(prompt, log):
    if not _secret("HUGGINGFACE_API_KEY"):
        log.append("⚪ Vídeo: Sem HUGGINGFACE_API_KEY")
        return None
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=300)
        out = client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-1.3B")
        log.append("✅ Vídeo gerado via Hugging Face")
        return out if isinstance(out, bytes) else out.read() if hasattr(out, "read") else None
    except Exception as e:
        log.append(f"❌ Erro Vídeo: {e}")
        return None

# ============================================================
# GITHUB (AUTO-EVOLUÇÃO)
# ============================================================
def _obter_token_github(): return _secret("GITHUB_TOKEN") or _secret("GITHUB_PAT") or _secret("GITHUB_API_KEY")

def zeus_salvar_codigo_github(caminho_arquivo: str, codigo_conteudo: str, mensagem: str = "Zeus 5.0 Auto-Update"):
    token = _obter_token_github(); user = _secret("GITHUB_USER"); repo = _secret("GITHUB_REPO")
    if not (token and user and repo): return {"status": "erro", "mensagem": "❌ Faltam credenciais do GitHub nos Secrets."}
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp_get = requests.get(url, headers=headers, timeout=25)
        sha = resp_get.json().get("sha") if resp_get.status_code == 200 else None
        payload = {"message": mensagem, "content": base64.b64encode(codigo_conteudo.encode("utf-8")).decode("utf-8")}
        if sha: payload["sha"] = sha
        resp_put = requests.put(url, headers=headers, json=payload, timeout=25)
        if resp_put.status_code in (200, 201): return {"status": "sucesso", "mensagem": f"✅ GitHub sincronizado em '{caminho_arquivo}'.", "url": resp_put.json().get("content", {}).get("html_url", "")}
        return {"status": "erro", "mensagem": f"❌ Erro GitHub HTTP {resp_put.status_code}: {resp_put.text[:150]}"}
    except Exception as e: return {"status": "erro", "mensagem": f"❌ Erro de conexão com GitHub: {str(e)[:150]}"}

def zeus_listar_memoria_github(pasta: str = "nucleo_zeus/modulos"):
    token = _obter_token_github(); user = _secret("GITHUB_USER"); repo = _secret("GITHUB_REPO")
    if not (token and user and repo): return []
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{pasta}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200: return [item['path'] for item in resp.json() if item.get('type') == 'file']
    except Exception: pass
    return []

def zeus_ler_arquivo_github(caminho_arquivo: str):
    token = _obter_token_github(); user = _secret("GITHUB_USER"); repo = _secret("GITHUB_REPO")
    if not (token and user and repo): return None
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200: return base64.b64decode(resp.json()["content"]).decode("utf-8")
    except Exception: pass
    return None

def zeus_aprender_ensemble(instrucao: str, linguagem: str = "python") -> str:
    sys_gerar = f"Você é um engenheiro sênior em {linguagem}. Escreva código funcional, robusto, limpo e documentado. Responda APENAS com o código puro."
    ok, texto1, prov1, _ = get_llm_response(st.session_state.session_id, instrucao, "groq", system=sys_gerar, use_history=False)
    if not ok: return "❌ Falha ao gerar código via IA."
    codigo_v1 = re.sub(r"^[a-zA-Z]*\n", "", texto1)
    codigo_v1 = re.sub(r"\n$", "", codigo_v1).strip()
    slug = "".join(c if c.isalnum() else "" for c in instrucao[:25]).lower() or "modulo"
    caminho = f"nucleo_zeus/modulos/modulo_{slug}.py"
    res_save = zeus_salvar_codigo_github(caminho, codigo_v1, f"Zeus auto: {instrucao[:40]}")
    if res_save["status"] == "sucesso": return f"✅ Módulo gerado por *{prov1}* e salvo!\n[Ver no GitHub]({res_save['url']})"
    return f"❌ Falha ao salvar: {res_save['mensagem']}"

# ============================================================
# SPOTIFY (CORRIGIDO)
# ============================================================
def zeus_tocar_musica(nome_musica: str) -> str:
    if not spotipy: return "❌ Biblioteca 'spotipy' não instalada."
    try:
        client_id = st.secrets.get("SPOTIFY_CLIENT_ID")
        client_secret = st.secrets.get("SPOTIFY_CLIENT_SECRET")
        refresh_token = st.secrets.get("SPOTIFY_REFRESH_TOKEN")
        
        if not (client_id and client_secret and refresh_token): 
            return "❌ Credenciais do Spotify ausentes nos Secrets."
            
        auth_manager = SpotifyOAuth(
            client_id=client_id, 
            client_secret=client_secret, 
            redirect_uri="https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/", 
            scope="user-modify-playback-state user-read-playback-state", 
            cache_path=None
        )
        token_info = auth_manager.refresh_access_token(refresh_token)
        sp = spotipy.Spotify(auth=token_info["access_token"])
        
        results = sp.search(q=nome_musica, limit=1, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        
        if not tracks: 
            return f"❌ Nenhuma faixa encontrada para '{nome_musica}'."
            
        sp.start_playback(uris=[tracks[0]["uri"]])
        return f"✅ Tocando: {tracks[0]['name']} - {tracks[0]['artists'][0]['name']}"
    except Exception as e: 
        return f"❌ Erro Spotify: {str(e)[:120]}"
        # ============================================================
# AVATAR COMPONENT (ESFERA E RAIOS 3D)
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
  h1 { font-size: 1.4rem; margin: 0; text-shadow: 0 0 12px #00ffff; letter-spacing: 2px; }
  p { font-size: 0.85rem; color: #ff00ff; margin: 6px 0 0 0; }
  .chat-container { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); width: 94%; max-width: 650px; background: rgba(3,3,15,0.9); border: 1px solid #00ffff; border-radius: 8px; padding: 12px; z-index: 10; box-sizing: border-box; box-shadow: 0 0 20px rgba(0,255,255,0.2); }
  .chat-history { height: 110px; overflow-y: auto; font-size: 0.85rem; margin-bottom: 8px; display: flex; flex-direction: column; gap: 6px; }
  .message { padding: 6px 10px; border-radius: 4px; max-width: 85%; }
  .user-msg { background: rgba(255,0,255,0.15); border-left: 3px solid #ff00ff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(0,255,255,0.15); border-left: 3px solid #00ffff; color: #00ffff; align-self: flex-start; }
  .input-group { display: flex; gap: 6px; }
  input[type="text"] { flex-grow: 1; background: rgba(0,0,0,0.7); border: 1px solid #00ffff; color: #00ffff; padding: 8px; border-radius: 4px; outline: none; }
  button { background: linear-gradient(135deg, #00ffff, #ff00ff); border: none; color: #030308; font-weight: bold; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="three-box"></div>
<canvas id="c2"></canvas>
<div class="ui-overlay">
  <h1 id="title">[ ZEUS 5.0 // CYBER-CORE ]</h1>
  <p id="status">STATUS: ONLINE & SECURE</p>
</div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <input type="text" id="userInput" placeholder="Comando neural..." />
    <button id="sendBtn">Transmitir</button>
  </div>
</div>
<script>
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 650});
let avatar = "sphere", lastReplyId = 0, mouseX = 0, mouseY = 0;
const box = document.getElementById("three-box"), cv = document.getElementById("c2"), ctx = cv.getContext("2d");
window.addEventListener("mousemove", (e) => { mouseX = (e.clientX / window.innerWidth) * 2 - 1; mouseY = -(e.clientY / window.innerHeight) * 2 + 1; });
window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.avatar && a.avatar !== avatar) { avatar = a.avatar; updateAvatarMode(); }
  if (a.reply_id !== lastReplyId) { lastReplyId = a.reply_id; if (a.reply) addMessage(a.reply, "ai"); }
});
function updateAvatarMode() { box.style.display = (avatar === "sphere" || avatar === "gods_lightning") ? "block" : "none"; cv.style.display = (avatar === "sphere" || avatar === "gods_lightning") ? "none" : "block"; }
function addMessage(text, sender) { const h = document.getElementById("chatHistory"); const d = document.createElement("div"); d.classList.add("message", sender === "user" ? "user-msg" : "ai-msg"); d.textContent = text; h.appendChild(d); h.scrollTop = h.scrollHeight; }
document.getElementById("sendBtn").addEventListener("click", submitText);
document.getElementById("userInput").addEventListener("keydown", function(e) { if(e.key === "Enter") submitText(); });
function submitText() { const input = document.getElementById("userInput"); const val = input.value.trim(); if(!val) return; addMessage(val, "user"); input.value = ""; sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: val }, dataType: "json" }); }
const HAS3 = typeof THREE !== "undefined";
let scene, camera, renderer, sphere, lightningGroup;
if (HAS3) {
  scene = new THREE.Scene(); camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000); camera.position.z = 6;
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  box.appendChild(renderer.domElement);
  sphere = new THREE.Mesh(new THREE.SphereGeometry(1.8, 32, 32), new THREE.MeshBasicMaterial({ color: 0x00ffff, wireframe: true }));
  scene.add(sphere);
  lightningGroup = new THREE.Group(); scene.add(lightningGroup);
  for(let i=0; i<5; i++) {
    const geom = new THREE.BufferGeometry();
    const pts = [new THREE.Vector3(0,0,0), new THREE.Vector3((Math.random()-0.5)*4, (Math.random()-0.5)*4, (Math.random()-0.5)*4)];
    geom.setFromPoints(pts);
    const line = new THREE.Line(geom, new THREE.LineBasicMaterial({ color: 0xffd700, linewidth: 3 }));
    lightningGroup.add(line);
  }
}
let lightningTimer = 0;
function animate() {
  requestAnimationFrame(animate);
  if (HAS3) {
    renderer.setSize(box.clientWidth || 300, box.clientHeight || 300);
    if (avatar === "sphere") {
      sphere.visible = true; lightningGroup.visible = false;
      sphere.rotation.y += 0.01 + mouseX * 0.05; sphere.rotation.x += mouseY * 0.05;
      renderer.render(scene, camera);
    } else if (avatar === "gods_lightning") {
      sphere.visible = false; lightningGroup.visible = true;
      lightningTimer++;
      if (lightningTimer % 10 === 0) {
        lightningGroup.children.forEach(line => {
          const positions = line.geometry.attributes.position.array;
          positions[3] = (Math.random() - 0.5) * 5 + mouseX * 2;
          positions[4] = (Math.random() - 0.5) * 5 + mouseY * 2;
          positions[5] = (Math.random() - 0.5) * 5;
          line.geometry.attributes.position.needsUpdate = true;
        });
      }
      lightningGroup.rotation.y += 0.02;
      renderer.render(scene, camera);
    } else {
      box.style.display = "none"; ctx.fillStyle = "#030308"; ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.fillStyle = "#00ffff"; ctx.font = "20px monospace";
      ctx.fillText(avatar === "eye" ? "[ OLHO CIBERNÉTICO ATIVO ]" : "[ NÚCLEO ROBÔ DIGITAL ]", 40, 60);
    }
  }
}
animate();
</script>
</body>
</html>
"""

@st.cache_resource
def _avatar_component(_hash: str):
    pasta = os.path.join(tempfile.gettempdir(), f"zeus_core_{_hash}")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "index.html"), "w", encoding="utf-8") as f:
        f.write(AVATAR_HTML)
    return components.declare_component(f"zeus_core_{_hash}", path=pasta)

AVATAR_MAP = {
    "Esfera 3D (Cyberpunk)": "sphere",
    "⚡ Deuses Jogando Raios (Olimpo Cibernético)": "gods_lightning",
    "Olho Cibernético": "eye",
    "Robô Digital": "robot"
}

def render_avatar_core(avatar_key):
    comp = _avatar_component(hashlib.md5(AV_HASH := AVATAR_HTML.encode("utf-8")).hexdigest()[:8])
    ev = comp(avatar=avatar_key, reply=st.session_state.get("avatar_reply"), reply_id=st.session_state.get("avatar_reply_id", 0), key="zeus_main_core", default=None)
    if ev and ev.get("id") != st.session_state.get("last_event_id"):
        st.session_state.last_event_id = ev["id"]
        texto = ev.get("text")
        if texto:
            resposta = processar_prompt(texto)
            st.session_state.avatar_reply = resposta
            st.session_state.avatar_reply_id = ev["id"]
            st.rerun()

# ============================================================
# APP PRINCIPAL E INTERFACE LATERAL
# ============================================================
init_db()
if "session_id" not in st.session_state: st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state: st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus 5.0")
    theme_name = st.selectbox("🎨 Tema Visual", list(THEMES.keys()), index=0)
    avatar_label = st.selectbox("🤖 Avatar Ativo", list(AVATAR_MAP.keys()), index=0)
    avatar_key = AVATAR_MAP[avatar_label]
    model_choice = st.selectbox("🧠 Modelo LLM Principal", PROVIDERS, index=0, key="model_sel")
    
    st.divider()
    st.subheader("🎵 Controle Spotify")
    musica_input = st.text_input("Nome da faixa:", key="sidebar_musica")
    if st.button("▶️ Tocar no Spotify"):
        if musica_input.strip():
            st.toast(zeus_tocar_musica(musica_input), icon="🎵")

    st.divider()
    st.subheader("📂 Módulos no GitHub")
    if st.button("🔄 Atualizar Lista de Módulos"):
        st.session_state.modulos_github = zeus_listar_memoria_github()
    for mod in st.session_state.get("modulos_github", []):
        st.text(f"• {mod}")

    st.divider()
    if st.button("🗑️ Limpar Sessão"):
        st.session_state.messages = []
        st.session_state.avatar_reply = None
        st.rerun()

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE 5.0 (ENTERPRISE)")

render_avatar_core(avatar_key)

with st.expander("⚡ Núcleo de Auto-Evolução e Sincronização GitHub", expanded=False):
    instrucao = st.text_input("O que o Zeus deve desenvolver e gravar no repositório GitHub?", placeholder="Ex: criar script para análise financeira...")
    linguagem = st.selectbox("Linguagem", ["python", "javascript", "cpp", "java"])
    if st.button("🚀 Gerar e Salvar no GitHub"):
        if instrucao.strip():
            with st.spinner("Gerando módulo sênior e sincronizando com GitHub..."):
                res = zeus_aprender_ensemble(instrucao.strip(), linguagem)
            st.markdown(res)
        else:
            st.warning("Insira uma instrução válida.")

with st.expander("📜 Histórico Completo de Mensagens"):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
