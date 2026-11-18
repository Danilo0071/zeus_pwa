# ============================================================
# ZEUS 3.0 - app.py (Versão Definitiva e Integrada)
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

def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", v).strip("\'"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets"):
            val = st.secrets.get(key)
            if not val:
                if key in ["SMTP_USER", "EMAIL_FROM"] and "email" in st.secrets:
                    val = st.secrets["email"].get("remetente")
                elif key == "SMTP_PASS" and "email" in st.secrets:
                    val = st.secrets["email"].get("senha_app")
                elif key == "TELEGRAM_BOT_TOKEN" and "telegram" in st.secrets:
                    val = st.secrets["telegram"].get("token")
                elif key == "TELEGRAM_CHAT_ID" and "telegram" in st.secrets:
                    val = st.secrets["telegram"].get("chat_id")
            if val:
                return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default)

SYS_PROMPT = (
    "Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
    "Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
    "Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico quando preciso. "
    "Você evolui continuamente: aprende com outras IAs, com a web e com o usuário. "
    "Responda em português. Seja objetivo."
)

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
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest"],
    "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3"],
    "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini"],
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
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700},
        timeout=60,
    )
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
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

def _call_openai_compat(url, api_key, model, msgs, system=None):
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700},
        timeout=60,
    )
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text

CALLERS = {
    "groq": _call_groq,
    "gemini": lambda k, m, msgs, sys: _call_gemini(k, m, msgs, sys, "v1"),
    "claude": lambda k, m, msgs, sys: (501, "Claude não configurado nesta rota"),
    "huggingface": lambda k, m, msgs, sys: _call_openai_compat("https://router.huggingface.co/v1/chat/completions", k, m, msgs, sys),
    "github": lambda k, m, msgs, sys: _call_openai_compat("https://models.github.ai/inference/chat/completions", k, m, msgs, sys),
    "deepinfra": lambda k, m, msgs, sys: _call_openai_compat("https://api.deepinfra.com/v1/openai/chat/completions", k, m, msgs, sys),
    "openrouter": lambda k, m, msgs, sys: _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", k, m, msgs, sys),
}

def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos"
    return text[:120]

def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}])
    order = [model_choice] + [p for p in PROVIDERS if p != model_choice]
    erros = []
    for prov in order:
        api_key = _secret(KEYS[prov])
        if not api_key:
            continue
        override = _secret(MODEL_SECRET[prov])
        candidates = [override] if override else MODELS.get(prov, [])
        try:
            ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs, system))
        except Exception as e:
            ok, text = False, f"erro: {e}"
        if ok:
            return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

# ============================================================
# COMANDOS REAIS DO TELEGRAM (Sem Teatro, 100% Autônomo para Danilo)
# ============================================================
def zf_enviar_telegram(texto: str) -> str:
    token = st.secrets.get("TELEGRAM_BOT_TOKEN") or st.secrets.get("telegram", {}).get("token", "")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID") or st.secrets.get("telegram", {}).get("chat_id", "")
    
    if not token or not chat_id:
        return "❌ ERRO CRÍTICO: Token do Telegram ou Chat ID não encontrados no secrets.toml."
    
    texto = (texto or "").strip()
    if not texto:
        texto = "Teste de telemetria e protocolo de bunker acionado por Danilo."
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🚨 *[ZEUS - PROTOCOLO DE BUNKER]* 🚨\n\nCriador: *Danilo*\n\n{texto}",
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200 and r.json().get("ok"):
            return "✅ SUCESSO REAL: Mensagem disparada diretamente nos servidores do Telegram para o Danilo!"
        else:
            return f"❌ FALHA NA API DO TELEGRAM (Status {r.status_code}): {r.text}"
    except Exception as e:
        return f"❌ EXCEÇÃO DE REDE NO TELEGRAM: {str(e)}"

def _rt_comando_telegram(texto):
    t = re.sub(
        r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*",
        "",
        texto,
    ).strip(" .,!?:-")
    if not t:
        t = "Alerta de telemetria e bunker disparado pelo criador Danilo."
    return zf_enviar_telegram(t)

def interceptar_comando_autonomo(texto):
    low = texto.lower()
    if "telegram" in low or "tg" in low or "bunker" in low or "telemetria" in low:
        return True, _rt_comando_telegram(texto)
    return False, ""

def processar_prompt(prompt):
    tratado, resposta = interceptar_comando_autonomo(prompt)
    sid = st.session_state.session_id
    if tratado:
        st.session_state.messages.append({"role": "user", "content": prompt})
        add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        add_message(sid, "assistant", resposta)
        st.session_state.last_info = "⚡ Comando autônomo executado com sucesso"
        return resposta
    
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})
    ok, response, prov, erros = get_llm_response(sid, prompt, escolhido)
    if not ok:
        response = "Não consegui contatar as IAs. Verifique as chaves e conexão."
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    add_message(sid, "assistant", response)
    return response

# ============================================================
# APP UI
# ============================================================
init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0)
    model_choice = st.selectbox("🧠 Modelo LLM", PROVIDERS, index=0, key="model_sel")
    if st.button("🗑️ Limpar Conversa"):
        st.session_state.messages = []
        st.rerun()

inject_theme("Cyberpunk")
st.title("⚡ ZEUS // NÚCLEO DE OPERAÇÕES DO DANILO")
st.markdown("Bem-vindo de volta, **Danilo**. O sistema está blindado e conectado ao Telegram.")

prompt_usuario = st.chat_input("Comando para o Zeus (ex: 'Zeus, envia no telegram: Teste concluído')...")
if prompt_usuario:
    processar_prompt(prompt_usuario)
    st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
