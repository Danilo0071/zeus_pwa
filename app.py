# ============================================================
# ZEUS 3.0 - app.py
# CHAVES: nunca coloque no código. Use .streamlit/secrets.toml (e adicione ao .gitignore)
# ou Settings > Secrets no Streamlit Cloud:
#   GROQ_API_KEY = "..."          GEMINI_API_KEY = "..."
#   ANTHROPIC_API_KEY = "..."     HUGGINGFACE_API_KEY = "..."
#   GITHUB_API_KEY = "..."        (token com acesso ao GitHub Models)
#   DEEPINFRA_API_KEY = "..."     (deepinfra.com/dash/api_keys: Dolphin, imagem e edição de foto)
#   OPENROUTER_API_KEY = "..."    (openrouter.ai/keys)
#   GITHUB_USER = "..."  GITHUB_REPO = "..."   (auto-evolução: token com Contents: Read and write)
# Opcionais (busca Google oficial): GOOGLE_API_KEY e GOOGLE_CSE_ID
# Dependências: streamlit requests edge-tts duckduckgo-search huggingface_hub openai
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
    from openai import OpenAI  # cliente OpenAI (usado para DeepInfra)
except ImportError:
    OpenAI = None
import streamlit as st
import streamlit.components.v1 as components

# Importação dinâmica das habilidades geradas pelo Zeus e do gerenciador do GitHub
try:
    import zeus_skills
except Exception:
    pass
try:
    from github_manager import salvar_no_github
except Exception:
    salvar_no_github = None

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


def inject_theme_classico(theme_name: str):
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
# CHAVES
# ============================================================
CHAVES_PADRAO = {}


def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", v).strip('"\'')
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v


def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key) if hasattr(st, "secrets") else None
        if val:
            return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))


# ============================================================
# ZEUS E-MAIL REAL (Integrado e corrigido)
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
# CLIENTE LLM (GROQ, GEMINI, CLAUDE) COM RETENTATIVA E FALLBACK
# ============================================================
SYS_PROMPT = (
    "Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
    "Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
    "Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico quando preciso. "
    "Você evolui continuamente: aprende com outras IAs, com a web e com o usuário. "
    "Quando as IAs falham, você busca na internet por conta própria e responde com o que encontrar. "
    "Você pode enviar mensagens no Telegram e e-mails quando o usuário pedir. "
    "Responda em português. Seja objetivo (respostas serão faladas em voz alta quando possível)."
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


def _groq_payload(model, msgs, system=None):
    p = {"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}
    if model.startswith("openai/gpt-oss"):
        p["reasoning_effort"] = "low"
    return p


def _call_groq(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_groq_payload(model, msgs, system),
        timeout=60,
    )
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text


def _call_claude(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs},
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
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700},
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
                model=model, temperature=0.4, max_tokens=700,
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
            return "✅ SUCESSO REAL: Mensagem disparada diretamente nos servidores du Telegram para o Danilo!"
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

def interceptar_comando_autonomo_telegram(texto):
    low = texto.lower()
    if "telegram" in low or "tg" in low or "bunker" in low or "telemetria" in low:
        return True, _rt_comando_telegram(texto)
    return False, ""


def processar_prompt(prompt):
    # Interceptação Telegram
    tratado, resposta = interceptar_comando_autonomo_telegram(prompt)
    if not tratado:
        # Interceptação E-mail
        tratado, resposta = interceptar_comando_autonomo_email(prompt)

    if tratado:
        sid = st.session_state.session_id
        st.session_state.messages.append({"role": "user", "content": prompt})
        add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        add_message(sid, "assistant", resposta)
        st.session_state.last_info = "⚡ Comando autônomo executado com sucesso"
        return resposta

    sid = st.session_state.session_id
    escolhido = st.session_state.get("model_sel", "groq")
    st.session_state.messages.append({"role": "user", "content": prompt})

    prompt_llm, fonte_web = enriquecer_com_web(prompt)
    ok, response, prov, erros = get_llm_response(sid, prompt_llm, escolhido)

    if not ok:
        response = "Não consegui contatar as IAs no momento. Verifique suas chaves de API nos Secrets."

    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    add_message(sid, "assistant", response)
    return response


# ============================================================
# RECONHECIMENTO E VOZ NEURAL (Mantidos integralmente)
# ============================================================
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
            data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0"},
            timeout=60,
        )
        if r.status_code != 200:
            return "", f"Transcrição falhou ({r.status_code})"
        return r.json().get("text", "").strip(), ""
    except Exception as e:
        return "", f"Erro: {e}"


def _limpar_para_fala(texto: str, limite: int = 700) -> str:
    texto = re.sub(r"```.*?```", " (código omitido) ", texto, flags=re.S)
    return re.sub(r"[*#`_>~|]", "", texto).strip()


async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate, volume="+0%", pitch="+0Hz").stream():
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
    except Exception:
        return None


FALLBACK_VOZES = {
    "Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural",
    "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural",
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
# BUSCA NA INTERNET E MÍDIA (Mantidas integralmente)
# ============================================================
def web_search(q, n=5):
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as d:
            res = list(d.text(q, max_results=n))
        return "\n".join("- " + x.get("title", "") + ": " + x.get("body", "") for x in res), "DuckDuckGo"
    except Exception:
        return "", ""

def precisa_web(prompt):
    return True

def enriquecer_com_web(prompt):
    ctx_web, fonte = web_search(prompt[:300])
    if not ctx_web:
        return prompt, ""
    return "[DADOS DA INTERNET]\n" + ctx_web[:3500] + "\n\nPergunta: " + prompt, fonte

def make_image(prompt, log):
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=120)
        img = client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.append(str(e))
        return None


# ============================================================
# APP PRINCIPAL (Mantendo o layout original exato do painel)
# ============================================================
def main():
    init_db()
    inject_theme("Cyberpunk")

    if "session_id" not in st.session_state:
        st.session_state.session_id = "default_session"
    if "messages" not in st.session_state:
        st.session_state.messages = []

    st.sidebar.title("⚡ Painel de Controle")
    st.sidebar.info("Criador: Danilo\ne-mail: daniloemanueldasilva078872@gmail.com")

    st.title("⚡ Zeus 3.0 - Núcleo Autônomo")
    
    # Exibir histórico
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
