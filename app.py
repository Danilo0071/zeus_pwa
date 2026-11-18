# ============================================================
# ZEUS 3.0 - app.py (Versão Consolidada & Otimizada - Sem Travas)
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
import zipfile

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
    v = re.sub(r"\s+", "", v).strip(""'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val:
            return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))

SYS_PROMPT = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico. Respostas dinâmicas e completas."

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

def processar_prompt(prompt):
    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    prompt_llm, fonte_web = enriquecer_com_web(prompt)
    ok, response, prov, erros = get_llm_response(sid, prompt_llm, escolhido)
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    if ok:
        add_message(sid, "assistant", response)
        st.session_state.last_info = (
            f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "") + (" · 🌐 web: " + fonte_web if fonte_web else "")
        )
    else:
        st.session_state.last_info = "❌ " + " | ".join(erros)
    return response

def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    api_key = _secret("GROQ_API_KEY")
    if not api_key:
        return "", "GROQ_API_KEY ausente."
    if not audio_bytes:
        return "", "Áudio vazio."
    base = (mime or "audio/webm").split(";")[0]
    ext = "mp4" if "mp4" in base else "ogg" if "ogg" in base else "webm"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (f"audio.{ext}", audio_bytes, base)},
            data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0", "prompt": "Conversa com o assistente Zeus."},
            timeout=60,
        )
        if r.status_code != 200:
            return "", f"Transcrição falhou ({r.status_code}): {r.text[:120]}"
        texto = r.json().get("text", "").strip()
        return texto, ""
    except Exception as e:
        return "", f"Erro na transcrição: {e}"

def _limpar_para_fala(texto: str, limite: int = 900) -> str:
    texto = re.sub(r"```.*?```", " (código omitido) ", texto, flags=re.S)
    texto = re.sub(r"[*#`_>~|]", "", texto).strip()
    return texto

PITCH_ATUAL = "+0Hz"
VOLUME_ATUAL = "+0%"

async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate, volume=VOLUME_ATUAL, pitch=PITCH_ATUAL).stream():
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

WEB_TRIGGERS = (
    "hoje", "agora", "atual", "atualmente", "últimas", "ultimas", "notícia", "noticia", "recente",
    "pesquise", "pesquisa", "busque", "procure", "google", "internet", "na web", "cotação", "cotacao",
    "dólar", "dolar", "preço", "preco", "clima", "previsão", "previsao", "resultado", "placar",
)

def _search_google_cse(q, n=5):
    key, cx = _secret("GOOGLE_API_KEY"), _secret("GOOGLE_CSE_ID")
    if not (key and cx):
        return ""
    r = requests.get("https://www.googleapis.com/customsearch/v1", params={"key": key, "cx": cx, "q": q, "num": n, "hl": "pt"}, timeout=20)
    if r.status_code != 200:
        return ""
    linhas = []
    for i in r.json().get("items", []):
        linhas.append("- " + i.get("title", "") + ": " + i.get("snippet", "") + " (" + i.get("link", "") + ")")
    return "\n".join(linhas)

def _search_ddg(q, n=5):
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as d:
            res = list(d.text(q, max_results=n))
        linhas = []
        for x in res:
            linhas.append("- " + x.get("title", "") + ": " + x.get("body", "") + " (" + x.get("href", "") + ")")
        return "\n".join(linhas)
    except Exception:
        return ""

def web_search(q, n=5):
    for nome, fn in (("Google CSE", _search_google_cse), ("DuckDuckGo", _search_ddg)):
        try:
            txt = fn(q)
        except Exception:
            txt = ""
        if txt and txt.strip():
            return txt.strip(), nome
    return "", ""

def precisa_web(prompt):
    modo = st.session_state.get("web_mode", "Automática")
    if modo == "Desligada":
        return False
    if modo == "Sempre":
        return True
    low = prompt.lower()
    return any(t in low for t in WEB_TRIGGERS)

def enriquecer_com_web(prompt):
    if not precisa_web(prompt):
        return prompt, ""
    ctx_web, fonte = web_search(prompt[:300])
    if not ctx_web:
        return prompt, ""
    hoje = time.strftime("%d/%m/%Y")
    novo = (
        "[DADOS DA INTERNET em " + hoje + ", via " + fonte + "]\n" + ctx_web[:3500] + "\n[FIM DOS DADOS]\n\n"
        "Use os dados acima se forem úteis. Pergunta: " + prompt
    )
    return novo, fonte

def make_image(prompt, log):
    key = _secret("HUGGINGFACE_API_KEY")
    if key:
        try:
            from huggingface_hub import InferenceClient
            client = InferenceClient(api_key=key, timeout=120)
            img = client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
            log.append("✅ Imagem gerada via HuggingFace (FLUX)")
            return io.BytesIO()
        except Exception as e:
            log.append(f"❌ HF Image: {e}")
    try:
        from urllib.parse import quote
        r = requests.get(f"https://image.pollinations.ai/prompt/{quote(prompt[:600])}", params={"width": 1024, "height": 1024, "nologo": "true"}, timeout=90)
        if r.status_code == 200:
            log.append("✅ Imagem gerada via Pollinations")
            return r.content
    except Exception as e:
        log.append(f"❌ Pollinations: {e}")
    return None

def zf_tocar_musica(nome_musica: str) -> str:
    if not spotipy:
        return "Biblioteca spotipy não instalada."
    return "Spotify simulado."

init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus 3.0")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel_side")
    model_choice = st.selectbox("🧠 Modelo LLM", PROVIDERS, index=0, key="model_sel_side")
    
    st.subheader("🎙️ Voz Neural")
    _vozes = listar_vozes_edge()
    voz_nome = st.selectbox("🗣️ Voz do Zeus", list(_vozes.keys()), key="voz_sel_side")
    tts_rate = st.slider("⚡ Velocidade da fala", 0.8, 1.5, 1.0, 0.05, key="rate_sel_side")
    silence_s = st.slider("⏱️ Silêncio (s)", 1, 10, 2, key="silence_sel_side")

    if st.button("🗑️ Limpar Conversa"):
        st.session_state.messages = []
        st.rerun()

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE 3.0")

with st.expander("📜 Histórico da Conversa", expanded=True):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
