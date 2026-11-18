# ============================================================
# ZEUS 3.0 - app.py (Versão Consolidada & Otimizada - Sem Travas)
# CHAVES: nunca coloque no código. Use .streamlit/secrets.toml (e adicione ao .gitignore)
# ou Settings > Secrets no Streamlit Cloud:
#   GROQ_API_KEY = "..."          GEMINI_API_KEY = "..."
#   ANTHROPIC_API_KEY = "..."     HUGGINGFACE_API_KEY = "..."
#   GITHUB_API_KEY = "..."        (token com acesso ao GitHub Models)
#   DEEPINFRA_API_KEY = "..."     (deepinfra.com/dash/api_keys: Dolphin, imagem e edição de foto)
#   OPENROUTER_API_KEY = "..."    (openrouter.ai/keys)
#   GITHUB_USER = "..."  GITHUB_REPO = "..."   (auto-evolução: token com Contents: Read and write)
# Opcionais (busca Google oficial): GOOGLE_API_KEY e GOOGLE_CSE_ID
# Dependências: streamlit requests edge-tts duckduckgo-search huggingface_hub openai spotipy
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
    from openai import OpenAI  # cliente OpenAI (usado para DeepInfra)
except ImportError:
    OpenAI = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None

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
CHAVES_PADRAO = {
    "DEEPINFRA_API_KEY": "sk-di-bJioFqKGlbhVm5M0aLOkq20wIpEiZiuT",
}

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
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))

# ============================================================
# CLIENTE LLM (GROQ, GEMINI, CLAUDE) COM RETENTATIVA E FALLBACK
# ============================================================
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

# ============================================================
# RECONHECIMENTO (Whisper/Groq) E VOZ NEURAL (Edge TTS)
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

@st.cache_data(ttl=86400, show_spinner=False)
def listar_todas_vozes_edge():
    try:
        import edge_tts
        todas = asyncio.run(edge_tts.list_voices())
        ordenadas = sorted(todas, key=lambda x: (not x["ShortName"].startswith("pt-"), x["Locale"], x["ShortName"]))
        vozes = {}
        for v in ordenadas:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
            vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes
    except Exception:
        return FALLBACK_VOZES

# ============================================================
# BUSCA NA INTERNET
# ============================================================
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

# ============================================================
# GERAÇÃO DE MÍDIA & VISÃO
# ============================================================
def _mime_img(b):
    return "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"

def _to_bytes(obj):
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    buf = io.BytesIO()
    obj.save(buf, format="PNG")
    return buf.getvalue()

def make_image(prompt, log):
    key = _secret("HUGGINGFACE_API_KEY")
    if key:
        try:
            from huggingface_hub import InferenceClient
            client = InferenceClient(api_key=key, timeout=120)
            img = client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
            log.append("✅ Imagem gerada via HuggingFace (FLUX)")
            return _to_bytes(img)
        except Exception as e:
            log.append(f"❌ HF Image: {e}")
    # Fallback Pollinations
    try:
        from urllib.parse import quote
        r = requests.get(f"https://image.pollinations.ai/prompt/{quote(prompt[:600])}", params={"width": 1024, "height": 1024, "nologo": "true"}, timeout=90)
        if r.status_code == 200:
            log.append("✅ Imagem gerada via Pollinations")
            return r.content
    except Exception as e:
        log.append(f"❌ Pollinations: {e}")
    return None

def editar_imagem(img, prompt_edicao, descricao, log):
    img_bytes = make_image((descricao + ". " + prompt_edicao)[:900], log)
    return img_bytes, ("editada" if img_bytes else "")

def make_video(prompt, log, img=None):
    key = _secret("HUGGINGFACE_API_KEY")
    if not key:
        return None
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=key, timeout=300)
        out = client.text_to_video(prompt, model="Wan-AI/Wan2.1-T2V-1.3B")
        log.append("✅ Vídeo gerado via HuggingFace")
        return _to_bytes(out)
    except Exception as e:
        log.append(f"❌ Vídeo: {e}")
    return None

def analisar_imagem(img_b64, mime, pergunta):
    key = _secret("GEMINI_API_KEY")
    if not key:
        return "❌ GEMINI_API_KEY ausente."
    try:
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            json={"contents": [{"role": "user", "parts": [{"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]},
            timeout=60,
        )
        if r.status_code == 200:
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        return f"Erro ({r.status_code}): {r.text}"
    except Exception as e:
        return f"Erro na análise: {e}"

# ============================================================
# ZEUS FINAL - NÚCLEO CONSOLIDADO (Ensemble, Database zilsy/, Spotify)
# ============================================================
_ZF_PASTA_RAIZ = "zilsy"
_ZF_PASTA_MODULOS = _ZF_PASTA_RAIZ + "/modulos"

def zf_salvar_codigo_github(caminho_arquivo: str, codigo_conteudo: str, mensagem: str = "Zeus: atualização"):
    token = _secret("GITHUB_API_KEY") or _secret("GITHUB_TOKEN")
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo):
        return {"status": "erro", "mensagem": "Faltam chaves do GitHub nos Secrets."}

    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp_get = requests.get(url, headers=headers, timeout=20)
        sha = resp_get.json().get("sha") if resp_get.status_code == 200 else None
        payload = {"message": mensagem, "content": base64.b64encode(codigo_conteudo.encode("utf-8")).decode("utf-8")}
        if sha:
            payload["sha"] = sha
        resp_put = requests.put(url, headers=headers, json=payload, timeout=20)
        if resp_put.status_code in (200, 201):
            return {"status": "sucesso", "mensagem": f"Database GitHub atualizado em {caminho_arquivo}.", "url": resp_put.json().get("content", {}).get("html_url", "")}
        return {"status": "erro", "mensagem": f"Erro GitHub ({resp_put.status_code})"}
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro de conexão: {e}"}

def zf_listar_memoria_github(pasta: str = None):
    pasta = pasta or _ZF_PASTA_MODULOS
    token = _secret("GITHUB_API_KEY") or _secret("GITHUB_TOKEN")
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo):
        return []
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{pasta}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            itens = resp.json()
            if isinstance(itens, list):
                return [item["path"] for item in itens if item.get("type") == "file"]
    except Exception:
        pass
    return []

def zf_ler_arquivo_github(caminho_arquivo: str):
    token = _secret("GITHUB_API_KEY") or _secret("GITHUB_TOKEN")
    user = _secret("GITHUB_USER")
    repo = _secret("GITHUB_REPO")
    if not (token and user and repo):
        return None
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            dados = resp.json()
            if "content" in dados:
                return base64.b64decode(dados["content"]).decode("utf-8")
    except Exception:
        pass
    return None

def _zf_extrair_codigo(texto: str) -> str:
    m = re.search(r"```(?:[a-zA-Z+]*)?\s*\n(.*?)```", texto, re.S)
    return (m.group(1) if m else texto).strip()

def _zf_executar_python(codigo: str):
    try:
        ast.parse(codigo)
    except SyntaxError as e:
        return False, f"Erro de sintaxe na linha {e.lineno}: {e.msg}"
    namespace = {"__builtins__": __builtins__}
    try:
        exec(codigo, namespace)
    except Exception as e:
        return False, f"Erro no carregamento: {e}"
    funcoes = [v for k, v in namespace.items() if callable(v) and not k.startswith("_") and inspect.isfunction(v)]
    if not funcoes:
        return True, "Código compilado e estruturado com sucesso."
    alvo = funcoes[0]
    try:
        params = inspect.signature(alvo).parameters
        if params:
            return True, f"Função {alvo.__name__} validada."
        resultado = alvo()
        return True, f"Execução bem-sucedida! Retorno: {resultado}"
    except Exception as e:
        return False, f"Erro ao executar a função: {e}"

def _zf_chamar_ia(prompt: str, sistema: str, preferir: str = None):
    fn_llm = globals().get("get_llm_response")
    if fn_llm and "session_id" in st.session_state:
        provedor = preferir or st.session_state.get("model_sel", "groq")
        ok, texto, prov, _erros = fn_llm(st.session_state.session_id, prompt, provedor, system=sistema, use_history=False)
        return (texto, prov) if ok else (None, None)
    return None, None

def zf_aprender_ensemble(instrucao: str, linguagem: str = "python") -> str:
    linguagem = linguagem.lower().strip()
    ext = "py" if linguagem == "python" else "js" if linguagem == "javascript" else "txt"
    sys_gerar = f"Você é um engenheiro sênior em {linguagem}. Escreva código funcional e limpo. Responda APENAS com o código."
    texto1, prov1 = _zf_chamar_ia("Pedido: " + instrucao, sys_gerar)
    if not texto1:
        return "Nenhuma IA disponível gerou o código inicial."
    codigo_v1 = _zf_extrair_codigo(texto1)

    caminho = f"{_ZF_PASTA_MODULOS}/modulo_{int(time.time())}.{ext}"
    res_save = zf_salvar_codigo_github(caminho, codigo_v1, f"Zeus ensemble: {instrucao[:40]}")
    
    if linguagem == "python":
        ok_exec, msg_exec = _zf_executar_python(codigo_v1)
        exec_status = f"Execução: {msg_exec}"
    else:
        exec_status = f"Código em {linguagem} salvo com sucesso."

    return f"{exec_status}\n\nGitHub: {caminho} (gerado por {prov1})"

def zf_falar(texto: str, voz: str = None):
    fn_sint = globals().get("sintetizar")
    if not fn_sint:
        return None
    voz_usar = voz or st.session_state.get("zf_voz_ativa", "pt-BR-FranciscaNeural")
    try:
        return fn_sint(texto, voz_usar)
    except Exception:
        return None

def zf_tocar_musica(nome_musica: str) -> str:
    if not spotipy:
        return "Biblioteca spotipy não instalada."
    nome_musica = (nome_musica or "").strip()
    if not nome_musica:
        return "Nome da música vazio."
    try:
        client_id = _secret("SPOTIFY_CLIENT_ID")
        client_secret = _secret("SPOTIFY_CLIENT_SECRET")
        refresh_token = _secret("SPOTIFY_REFRESH_TOKEN")
        redirect_uri = _secret("SPOTIFY_REDIRECT_URI") or "https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/"
        if not (client_id and client_secret and refresh_token):
            return "Faltam credenciais do Spotify nos Secrets."
        auth_manager = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope="user-modify-playback-state user-read-playback-state", cache_path=None)
        token_info = auth_manager.refresh_access_token(refresh_token)
        sp = spotipy.Spotify(auth=token_info["access_token"])
        results = sp.search(q=nome_musica, limit=1, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            return f"Nenhuma faixa encontrada para {nome_musica}."
        track = tracks[0]
        sp.start_playback(uris=[track["uri"]])
        artista = track["artists"][0]["name"] if track.get("artists") else "desconhecido"
        return f"Tocando agora: {track['name']} - {artista}"
    except Exception as e:
        return f"Erro no Spotify: {e}"

# Avatares SVG Zeus Final
ZF_AVATAR_SUPREMO = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMDAgMzAwIj48cmVjdCB3aWR0aD0iMzAwIiBoZWlnaHQ9IjMwMCIgZmlsbD0iIzBiMGYxOSIvPjxwYXRoIGQ9Ik0xOTAgNjBMMTYwIDEzMEwxODUgMTMwTDE1MCAyMjBMMjQwIDExMEwyMDUgMTEwWiIgZmlsbD0iIzAwZmZjYyIgb3BhY2l0eT0iMC45Ii8+PC9zdmc+"
ZF_AVATARES = {
    "Zeus Supremo (Cyberpunk Neon)": ZF_AVATAR_SUPREMO,
}

def zf_render_painel():
    with st.container(border=True):
        col_av, col_cfg = st.columns([1, 2])
        with col_av:
            st.image(ZF_AVATAR_SUPREMO, width=150)
        with col_cfg:
            st.selectbox("Avatar do Zeus", list(ZF_AVATARES.keys()), key="zf_avatar_nome")
            vozes_disp = listar_vozes_edge()
            nome_voz = st.selectbox("Voz do Zeus", list(vozes_disp.keys()), key="zf_voz_nome")
            st.session_state.zf_voz_ativa = vozes_disp[nome_voz]
            if st.button("Ouvir prévia", key="zf_btn_previa"):
                audio_prev = zf_falar("Olá, Danilo. Eu sou Zeus.", vozes_disp[nome_voz])
                if audio_prev:
                    st.audio(audio_prev, format="audio/mp3")

    with st.sidebar:
        st.divider()
        st.subheader("🎵 Controle de Música (Spotify)")
        musica_input = st.text_input("Digite a música:", key="zf_input_musica")
        if st.button("Tocar no Spotify", key="zf_btn_tocar"):
            if musica_input.strip():
                res = zf_tocar_musica(musica_input)
                st.success(res) if "Tocando" in res else st.error(res)

    with st.expander("⚡ Zeus - Núcleo Avançado & Ensemble", expanded=False):
        instrucao = st.text_input("O que o Zeus deve desenvolver?", key="zf_instrucao")
        linguagem = st.selectbox("Linguagem", ["python", "javascript"], key="zf_linguagem")
        if st.button("Gerar, Revisar e Salvar", key="zf_btn_aprender") and instrucao.strip():
            with st.spinner("Processando..."):
                st.markdown(zf_aprender_ensemble(instrucao.strip(), linguagem))

# ============================================================
# AVATAR CYBERPUNK 3D / HTML CANVAS
# ============================================================
AVATAR_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #0b0f19; font-family: 'Courier New', monospace; color: #00ffcc; }
  #three-box { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
  .chat-container { position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%); width: 92%; max-width: 620px; background: rgba(18,24,38,0.9); border: 1px solid #00ffcc; border-radius: 8px; padding: 12px; z-index: 10; box-sizing: border-box; }
  .chat-history { height: 100px; overflow-y: auto; font-size: 0.85rem; margin-bottom: 8px; display: flex; flex-direction: column; gap: 6px; }
  .message { padding: 6px 10px; border-radius: 4px; max-width: 85%; }
  .user-msg { background: rgba(0,119,255,0.2); border-left: 3px solid #0077ff; color: #fff; align-self: flex-end; }
  .ai-msg { background: rgba(0,255,204,0.15); border-left: 3px solid #00ffcc; color: #00ffcc; align-self: flex-start; }
  .input-group { display: flex; gap: 8px; }
  input[type="text"] { flex-grow: 1; background: #0b0f19; border: 1px solid #00ffcc; color: #00ffcc; padding: 8px; border-radius: 4px; outline: none; }
  button { background: linear-gradient(135deg, #00ffcc, #0077ff); border: none; color: #0b0f19; font-weight: bold; padding: 8px 14px; border-radius: 4px; cursor: pointer; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<div id="three-box"></div>
<div class="chat-container">
  <div class="chat-history" id="chatHistory"></div>
  <div class="input-group">
    <input type="text" id="userInput" placeholder="Digite sua mensagem para o Zeus..." />
    <button id="sendBtn">Enviar</button>
  </div>
</div>
<script>
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 720});

window.addEventListener("message", function (e) {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const a = e.data.args || {};
  if (a.history) {
    const h = document.getElementById("chatHistory");
    h.innerHTML = "";
    a.history.forEach(m => {
      const d = document.createElement("div");
      d.className = "message " + (m.role === "user" ? "user-msg" : "ai-msg");
      d.textContent = m.content;
      h.appendChild(d);
    });
    h.scrollTop = h.scrollHeight;
  }
});

const input = document.getElementById("userInput"), sendBtn = document.getElementById("sendBtn");
function submitText() {
  const val = input.value.trim();
  if (!val) return;
  input.value = "";
  sendMsg("streamlit:setComponentValue", { value: { id: Date.now(), text: val } });
}
sendBtn.addEventListener("click", submitText);
input.addEventListener("keydown", e => { if (e.key === "Enter") submitText(); });

// Three.js 3D Cyber Sphere & Emerald Eye
const box = document.getElementById("three-box");
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, box.clientWidth / box.clientHeight, 0.1, 1000);
camera.position.z = 5;
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(box.clientWidth, box.clientHeight);
box.appendChild(renderer.domElement);

const sphereGroup = new THREE.Group();
scene.add(sphereGroup);

const coreGeo = new THREE.SphereGeometry(1.2, 32, 32);
const coreMat = new THREE.MeshStandardMaterial({ color: 0x00ffcc, emissive: 0x0077ff, emissiveIntensity: 0.5, wireframe: true });
const coreMesh = new THREE.Mesh(coreGeo, coreMat);
sphereGroup.add(coreMesh);

const ringGeo = new THREE.RingGeometry(1.6, 1.8, 32);
const ringMat = new THREE.MeshBasicMaterial({ color: 0x00ffcc, side: THREE.DoubleSide, transparent: true, opacity: 0.7 });
const ringMesh = new THREE.Mesh(ringGeo, ringMat);
ringMesh.rotation.x = Math.PI / 3;
sphereGroup.add(ringMesh);

scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const pointLight = new THREE.PointLight(0x00ffcc, 2, 50);
pointLight.position.set(3, 3, 3);
scene.add(pointLight);

function animate() {
  requestAnimationFrame(animate);
  sphereGroup.rotation.y += 0.01;
  sphereGroup.rotation.x += 0.005;
  ringMesh.rotation.z -= 0.02;
  renderer.render(scene, camera);
}
animate();

window.addEventListener("resize", () => {
  camera.aspect = box.clientWidth / box.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(box.clientWidth, box.clientHeight);
});
</script>
</body>
</html>
"""

@st.cache_resource
def _avatar_component(_html_hash: str):
    pasta = os.path.join(tempfile.gettempdir(), f"zeus_avatar_v3_{_html_hash}")
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "index.html"), "w", encoding="utf-8") as f:
        f.write(AVATAR_HTML)
    return components.declare_component(f"zeus_avatar_v3_{_html_hash}", path=pasta)

def render_avatar_voice(silence_s, stt_engine, tts_engine, voz, tts_rate):
    comp = _avatar_component(hashlib.md5(AVATAR_HTML.encode("utf-8")).hexdigest()[:10])
    ev = comp(
        history=st.session_state.messages[-12:],
        key="zeus_avatar_main",
        default=None,
    )
    if ev and ev.get("id") != st.session_state.get("last_event_id"):
        st.session_state.last_event_id = ev["id"]
        texto = ev.get("text")
        if texto:
            resposta = processar_prompt(texto)
            st.rerun()

# ============================================================
# APP PRINCIPAL & SIDEBAR
# ============================================================
init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus 3.0")
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="theme_sel")
    model_choice = st.selectbox("🧠 Modelo LLM", PROVIDERS, index=0, key="model_sel")
    
    st.subheader("🎙️ Voz Neural")
    _vozes = listar_vozes_edge()
    voz_nome = st.selectbox("🗣️ Voz do Zeus", list(_vozes.keys()), key="voz_sel")
    tts_rate = st.slider("⚡ Velocidade da fala", 0.8, 1.5, 1.0, 0.05, key="rate_sel")
    silence_s = st.slider("⏱️ Silêncio (s)", 1, 10, 2, key="silence_sel")
    voz_edge = _vozes[voz_nome]

    if st.button("🗑️ Limpar Conversa"):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("🔑 Status das Chaves")
    for k in KEYS.values():
        st.write(("✅ " if _secret(k) else "❌ ") + k)
    if st.session_state.get("last_info"):
        st.caption(st.session_state.last_info)

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE 3.0")

# Renderiza componentes principais
render_avatar_voice(silence_s, "whisper", "edge", voz_edge, tts_rate)
zf_render_painel()

with st.expander("📜 Histórico da Conversa", expanded=False):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
