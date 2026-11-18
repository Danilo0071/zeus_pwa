# ============================================================
# ZEUS 3.0 - app.py (Versão Definitiva - E-mail Real + Telegram)
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
import smtplib
from email.mime.text import MIMEText
import requests

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
import streamlit as st

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
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

def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(
        f"""
        <style>
            .stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {t['panel']}; }}
            h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}
        </style>
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

# ============================================================
# CHAVES E SECRETS
# ============================================================
def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", v).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val:
            return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default)

# ============================================================
# ZEUS E-MAIL REAL (SMTP Direto via Secrets)
# ============================================================
def zf_enviar_email_direto(texto: str) -> str:
    destinatario = "daniloemanueldasilva078872@gmail.com"
    try:
        remetente = st.secrets["email"]["remetente"]
        password = st.secrets["email"]["senha_app"]
    except Exception:
        return "❌ ERRO CRÍTICO: Seção [email] com remetente e senha_app não encontrada no secrets.toml."
    
    if not remetente or not password:
        return "❌ As chaves de e-mail na seção [email] estão vazias."
    
    corpo = re.sub(r"(?i).*?\b(e-?mail|email)\b", "", texto).strip()
    if not corpo or len(corpo) < 3:
        corpo = "Relatório automático do Zeus: Sistema 100% operacional para o criador Danilo."
        
    try:
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = "Status do Zeus - Alerta Automático"
        msg["From"] = remetente
        msg["To"] = destinatario

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls()
            s.login(remetente, password)
            s.sendmail(remetente, [destinatario], msg.as_string())
            
        return f"✅ E-MAIL ENVIADO COM SUCESSO: Mensagem disparada diretamente para `{destinatario}`."
    except Exception as e:
        return f"❌ EXCEÇÃO DE REDE NO SMTP: {str(e)}"

def _rt_comando_email(texto):
    return zf_enviar_email_direto(texto)

def interceptar_comando_autonomo_email(texto):
    low = texto.lower()
    if "email" in low or "e-mail" in low:
        return True, _rt_comando_email(texto)
    return False, ""

# ============================================================
# ZEUS TELEGRAM REAL
# ============================================================
def zf_enviar_telegram(texto: str) -> str:
    token = st.secrets.get("TELEGRAM_BOT_TOKEN") or st.secrets.get("telegram", {}).get("token", "")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID") or st.secrets.get("telegram", {}).get("chat_id", "")
    
    if not token or not chat_id:
        return "❌ ERRO CRÍTICO: Token do Telegram ou Chat ID não encontrados no secrets.toml."
    
    texto = (texto or "").strip()
    if not texto:
        texto = "Teste de telemetria acionado por Danilo."
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🚨 *[ZEUS - TELEMETRIA]* 🚨\n\nCriador: *Danilo*\n\n{texto}",
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200 and r.json().get("ok"):
            return "✅ SUCESSO REAL: Mensagem disparada no Telegram para o Danilo!"
        else:
            return f"❌ FALHA NO TELEGRAM (Status {r.status_code}): {r.text}"
    except Exception as e:
        return f"❌ EXCEÇÃO NO TELEGRAM: {str(e)}"

def _rt_comando_telegram(texto):
    t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*", "", texto).strip(" .,!?:-")
    if not t:
        t = "Alerta disparado pelo criador Danilo."
    return zf_enviar_telegram(t)

def interceptar_comando_autonomo_telegram(texto):
    low = texto.lower()
    if "telegram" in low or "tg" in low or "bunker" in low:
        return True, _rt_comando_telegram(texto)
    return False, ""

# ============================================================
# CLIENTE LLM E PROCESSAMENTO DE PROMPT
# ============================================================
SYS_PROMPT = (
    "Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
    "Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
    "Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico. "
    "Você evolui continuamente, aprende com outras IAs e executa ações reais no sistema. "
    "Responda em português. Seja objetivo."
)

PROVIDERS = ["groq", "gemini"]
KEYS = {
    "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest"],
}

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

def get_llm_response(session_id, prompt, model_choice):
    msgs = _normalize(get_recent_context(session_id) + [{"role": "user", "content": prompt}])
    api_key = _secret("GROQ_API_KEY")
    if api_key:
        code, text = _call_groq(api_key, "openai/gpt-oss-120b", msgs)
        if code == 200:
            return True, text, "groq", []
    
    api_key_gemini = _secret("GEMINI_API_KEY")
    if api_key_gemini:
        code, text = _call_gemini(api_key_gemini, "gemini-2.5-flash", msgs)
        if code == 200:
            return True, text, "gemini", []
            
    return False, "❌ Nenhum modelo de IA respondeu.", None, []

def processar_prompt(prompt):
    tratado, resposta = interceptar_comando_autonomo_telegram(prompt)
    if not tratado:
        tratado, resposta = interceptar_comando_autonomo_email(prompt)
    
    sid = st.session_state.session_id
    if tratado:
        st.session_state.messages.append({"role": "user", "content": prompt})
        add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        add_message(sid, "assistant", resposta)
        return resposta

    st.session_state.messages.append({"role": "user", "content": prompt})
    ok, response, prov, erros = get_llm_response(sid, prompt, "groq")
    if not ok:
        response = "Não consegui contatar as IAs no momento. Verifique suas chaves de API nos Secrets."
        
    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    add_message(sid, "assistant", response)
    return response

# ============================================================
# INTERFACE STREAMLIT
# ============================================================
def main():
    init_db()
    inject_theme("Cyberpunk")
    
    if "session_id" not in st.session_state:
        st.session_state.session_id = "default_session"
    if "messages" not in st.session_state:
        st.session_state.messages = []
        
    st.title("⚡ Zeus 3.0 - Núcleo Autônomo")
    st.sidebar.title("Painel de Controle")
    st.sidebar.info("Criador: Danilo\nE-mail: daniloemanueldasilva078872@gmail.com")
    
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            
    if prompt := st.chat_input("Digite um comando para o Zeus..."):
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.spinner("Zeus processando..."):
            resposta = processar_prompt(prompt)
        with st.chat_message("assistant"):
            st.markdown(resposta)

if __name__ == "__main__":
    main()
