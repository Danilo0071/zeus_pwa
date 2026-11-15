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
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

THEMES = {
    "Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"},
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
    for token, chave in (("__BG__", "bg"), ("__TEXT__", "text"), ("__PANEL__", "panel"), ("__PRIMARY__", "primary"), ("__BORDER__", "border")):
        css = css.replace(token, t[chave])
    st.markdown(css, unsafe_allow_html=True)




def inject_theme_classico(theme_name: str):
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
# Chave padrão: usada só se não houver Secrets/variável de ambiente. Troque quando quiser.
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
# - 503/500 (alta demanda): tenta de novo e depois o próximo modelo
# - Se o provedor escolhido falhar (ex.: Claude sem créditos), usa
#   automaticamente outro provedor que tenha chave.
# Para forçar um modelo: Secrets GROQ_MODEL, GEMINI_MODEL, ANTHROPIC_MODEL.
# ============================================================
SYS_PROMPT = "Você é Zeus, uma IA cyberpunk futurista e assistente sênior. Responda em português, de forma objetiva e com um tom tecnológico. Respostas curtas, pois serão faladas em voz alta."

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY", "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL", "huggingface": "HF_MODEL", "github": "GITHUB_MODEL", "deepinfra": "DEEPINFRA_MODEL", "openrouter": "OPENROUTER_MODEL"}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash"],
    "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5", "claude-sonnet-5-5"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["cognitivecomputations/dolphin-2.9-llama3-8b", "cognitivecomputations/dolphin-2.6-mixtral-8x7b", "meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3"],
    "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"],
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


def _groq_payload(model, msgs, system=None):
    p = {"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}
    if model.startswith("openai/gpt-oss"):
        p["reasoning_effort"] = "low"  # responde bem mais rápido
    return p


def _call_groq(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_groq_payload(model, msgs, system),
        timeout=60)
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text


def _call_claude(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs},
        timeout=60)
    if r.status_code == 200:
        return 200, "".join(b.get("text", "") for b in r.json()["content"])
    return r.status_code, r.text


def _call_gemini(api_key, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents},
        timeout=60)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text


def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    r = requests.post(
        url, headers=headers,
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs,
              "temperature": 0.4, "max_tokens": 700},
        timeout=60)
    if r.status_code == 200:
        return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text


def _call_huggingface(api_key, model, msgs, system=None):
    return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", api_key, model, msgs, system)


def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat("https://models.github.ai/inference/chat/completions", api_key, model, msgs, system,
                               {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})


def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=api_key, base_url=base, timeout=60)
            resp = client.chat.completions.create(
                model=model, temperature=0.4, max_tokens=700,
                messages=[{"role": "system", "content": system or SYS_PROMPT}] + msgs)
            return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)


def _call_openrouter(api_key, model, msgs, system=None):
    return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", api_key, model, msgs, system, {"X-Title": "Zeus 3.0"})


def _call_gemini_auto(api_key, model, msgs, system=None):
    code, texto = _call_gemini(api_key, model, msgs, system, "v1")
    if code in (400, 404):
        code2, texto2 = _call_gemini(api_key, model, msgs, system, "v1beta")
        if code2 == 200:
            return code2, texto2
    return code, texto


CALLERS = {"groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude,
           "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra, "openrouter": _call_openrouter}


def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return text[:120]


def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    """Retorna (ok, texto, provedor_usado, lista_de_erros)."""
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
        st.session_state.last_info = f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "") + (" · 🌐 web: " + fonte_web if fonte_web else "")
    else:
        st.session_state.last_info = "❌ " + " | ".join(erros)
    return response


# ============================================================
# RECONHECIMENTO (Whisper/Groq) E VOZ NEURAL (Edge TTS)
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    """Transcreve com Whisper (Groq). Retorna (texto, erro)."""
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
    texto = re.sub(r"```.*?```", " (código omitido) ", texto, flags=re.S)
    texto = re.sub(r"[*#`_>~|]", "", texto).strip()
    if len(texto) > limite:
        corte = texto[:limite]
        fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
        texto = corte[: fim + 1] if fim > 200 else corte
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
    """Gera MP3 com voz neural. Retorna bytes ou None."""
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
# BUSCA NA INTERNET (tempo real)
# Ordem: Google Custom Search (se configurado) -> Google Search via Gemini
# (grounding) -> DuckDuckGo -> Wikipédia.
# Secrets opcionais: GOOGLE_API_KEY + GOOGLE_CSE_ID (Programmable Search).
# ============================================================
WEB_TRIGGERS = (
    "hoje", "agora", "atual", "atualmente", "últimas", "ultimas", "notícia", "noticia", "recente",
    "pesquise", "pesquisa", "busque", "procure", "google", "internet", "na web", "cotação", "cotacao",
    "dólar", "dolar", "preço", "preco", "clima", "previsão", "previsao", "resultado", "placar",
    "quem ganhou", "quem é o atual", "lançamento", "lancamento", "versão mais nova", "ao vivo",
)


def _search_google_cse(q, n=5):
    key, cx = _secret("GOOGLE_API_KEY"), _secret("GOOGLE_CSE_ID")
    if not (key and cx):
        return ""
    r = requests.get("https://www.googleapis.com/customsearch/v1",
                     params={"key": key, "cx": cx, "q": q, "num": n, "hl": "pt"}, timeout=20)
    if r.status_code != 200:
        return ""
    linhas = []
    for i in r.json().get("items", []):
        linhas.append("- " + i.get("title", "") + ": " + i.get("snippet", "") + " (" + i.get("link", "") + ")")
    return "\n".join(linhas)


def _search_gemini_google(q):
    api_key = _secret("GEMINI_API_KEY")
    if not api_key:
        return ""
    modelos = [m for m in [_secret("GEMINI_SEARCH_MODEL")] if m] + ["gemini-2.5-flash", "gemini-flash-latest"]
    for model in modelos:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": "Pesquise no Google e responda em português com fatos atuais, datas e números: " + q}]}],
                    "tools": [{"google_search": {}}],
                },
                timeout=45)
            if r.status_code != 200:
                continue
            cand = r.json()["candidates"][0]
            texto = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
            fontes = []
            for c in cand.get("groundingMetadata", {}).get("groundingChunks", []):
                w = c.get("web", {})
                if w.get("title"):
                    fontes.append(w["title"])
            if texto:
                return texto + ("\nFontes: " + "; ".join(fontes[:5]) if fontes else "")
        except Exception:
            continue
    return ""


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


def _search_wiki(q, n=4):
    try:
        r = requests.get("https://pt.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n}, timeout=20)
        itens = r.json()["query"]["search"]
        return "\n".join("- " + i["title"] + ": " + re.sub(r"<[^>]+>", "", i["snippet"]) for i in itens)
    except Exception:
        return ""


def web_search(q, n=5):
    """Retorna (texto, fonte_usada). Texto vazio se nada funcionar."""
    for nome, fn in (("Google CSE", _search_google_cse), ("Google (Gemini)", _search_gemini_google),
                     ("DuckDuckGo", _search_ddg), ("Wikipédia", _search_wiki)):
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
    return any(t in low for t in WEB_TRIGGERS) or bool(re.search(r"\b20(2[4-9]|3[0-9])\b", low))


def enriquecer_com_web(prompt):
    """Retorna (prompt_para_o_LLM, fonte). Se não precisar/der, devolve o prompt original."""
    if not precisa_web(prompt):
        return prompt, ""
    ctx_web, fonte = web_search(prompt[:300])
    if not ctx_web:
        return prompt, ""
    hoje = time.strftime("%d/%m/%Y")
    novo = ("[DADOS DA INTERNET em " + hoje + ", via " + fonte + "]\n" + ctx_web[:3500] + "\n[FIM DOS DADOS]\n\n"
            "Use os dados acima se forem úteis e diga que vieram da internet. Pergunta do usuário: " + prompt)
    return novo, fonte


# ============================================================
# GERAÇÃO DE MÍDIA (imagem, edição de foto e vídeo) com fallback em cascata
# ============================================================
def _mime_img(b):
    return "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"


def _to_bytes(obj):
    """Aceita bytes ou imagem PIL e devolve bytes PNG."""
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    import io
    buf = io.BytesIO()
    obj.save(buf, format="PNG")
    return buf.getvalue()


def _hf_client(timeout=120):
    from huggingface_hub import InferenceClient
    return InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=timeout)


def _img_hf(prompt):
    if not _secret("HUGGINGFACE_API_KEY"):
        raise RuntimeError("sem chave")
    modelos = [m for m in [_secret("HF_IMAGE_MODEL")] if m] + ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"]
    erros = []
    try:
        client = _hf_client(120)
        for model in modelos:
            try:
                return _to_bytes(client.text_to_image(prompt, model=model))
            except Exception as e:
                erros.append(model.split("/")[-1] + ": " + str(e)[:80])
    except ImportError:
        erros.append("falta huggingface_hub no requirements.txt")
    for model in modelos:  # rota REST antiga, como reserva
        try:
            r = requests.post(f"https://router.huggingface.co/hf-inference/models/{model}",
                              headers={"Authorization": "Bearer " + _secret("HUGGINGFACE_API_KEY")},
                              json={"inputs": prompt}, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
            erros.append(f"REST {model.split('/')[-1]} ({r.status_code})")
        except Exception as e:
            erros.append("REST: " + str(e)[:60])
    raise RuntimeError(" | ".join(erros)[:300])


def _gemini_image_call(parts):
    key = _secret("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    model = _secret("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"Content-Type": "application/json", "x-goog-api-key": key},
                      json={"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
                      timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    for part in r.json()["candidates"][0]["content"]["parts"]:
        d = part.get("inlineData") or part.get("inline_data")
        if d:
            return base64.b64decode(d["data"])
    raise RuntimeError("Gemini não devolveu imagem (conta sem cota de imagem?)")


def _img_gemini(prompt):
    return _gemini_image_call([{"text": prompt}])


def _img_pollinations(prompt):
    from urllib.parse import quote
    ultimo = ""
    for tentativa in range(2):
        r = requests.get("https://image.pollinations.ai/prompt/" + quote(prompt[:600]),
                         params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
                         headers={"User-Agent": "Mozilla/5.0 (Zeus/3.0)"}, timeout=120)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
        ultimo = f"({r.status_code})"
        if r.status_code == 429 and tentativa == 0:
            time.sleep(16)  # plano anônimo: 1 pedido a cada ~15 s
            continue
        break
    raise RuntimeError(ultimo)


def _img_deepinfra(prompt):
    key = _secret("DEEPINFRA_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    modelo = _secret("DEEPINFRA_IMAGE_MODEL") or "black-forest-labs/FLUX-1-schnell"
    erros = []
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=key, base_url="https://api.deepinfra.com/v1/openai", timeout=120)
            resp = client.images.generate(model=modelo, prompt=prompt)
            return base64.b64decode(resp.data[0].b64_json)
        except Exception as e:
            erros.append("cliente OpenAI: " + str(e)[:100])
    for body in ({"prompt": prompt, "size": "1024x1024", "n": 1, "model": modelo}, {"prompt": prompt, "size": "1024x1024", "n": 1}):
        try:
            r = requests.post("https://api.deepinfra.com/v1/openai/images/generations",
                              headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code == 200:
                return base64.b64decode(r.json()["data"][0]["b64_json"])
            erros.append(f"REST ({r.status_code}) {r.text[:60]}")
        except Exception as e:
            erros.append("REST: " + str(e)[:60])
    raise RuntimeError(" | ".join(erros)[:300])


OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _or_headers(key):
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Title": "Zeus 3.0"}


def _or_extrair_imagem(js):
    msg = js["choices"][0]["message"]
    for im in (msg.get("images") or []):
        url = (im.get("image_url") or {}).get("url") or im.get("url", "")
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        if url.startswith("http"):
            return requests.get(url, timeout=60).content
    raise RuntimeError("OpenRouter não devolveu imagem")


def _img_openrouter(prompt):
    key = _secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    modelo = _secret("OPENROUTER_IMAGE_MODEL") or "google/gemini-2.5-flash-image"
    erros = []
    try:
        r = requests.post(OPENROUTER_BASE + "/images", headers=_or_headers(key), json={"model": modelo, "prompt": prompt}, timeout=120)
        if r.status_code == 200:
            return base64.b64decode(r.json()["data"][0]["b64_json"])
        erros.append(f"/images ({r.status_code}) {r.text[:70]}")
    except Exception as e:
        erros.append("/images: " + str(e)[:60])
    r = requests.post(OPENROUTER_BASE + "/chat/completions", headers=_or_headers(key),
                      json={"model": modelo, "messages": [{"role": "user", "content": prompt}], "modalities": ["image", "text"]}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(" | ".join(erros + [f"chat ({r.status_code}) {r.text[:70]}"]))
    try:
        return _or_extrair_imagem(r.json())
    except Exception as e:
        raise RuntimeError(" | ".join(erros + [str(e)]))


def _edit_openrouter(img, prompt):
    key = _secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    modelo = _secret("OPENROUTER_IMAGE_MODEL") or "google/gemini-2.5-flash-image"
    uri = "data:" + _mime_img(img) + ";base64," + base64.b64encode(img).decode()
    r = requests.post(OPENROUTER_BASE + "/chat/completions", headers=_or_headers(key), json={
        "model": modelo, "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": uri}}]}]},
        timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    return _or_extrair_imagem(r.json())


def _vis_openrouter(k, b, m, p):
    return _vis_openai(OPENROUTER_BASE + "/chat/completions", k, _secret("OPENROUTER_VISION_MODEL") or "google/gemini-2.5-flash",
                       b, m, p, extra={"X-Title": "Zeus 3.0"})


IMAGE_CASCADE = [("DeepInfra", _img_deepinfra), ("OpenRouter", _img_openrouter), ("Hugging Face", _img_hf), ("Gemini", _img_gemini),
                 ("Pollinations (sem chave)", _img_pollinations)]


def make_image(prompt, log):
    for nome, fn in IMAGE_CASCADE:
        try:
            data = fn(prompt)
            log.append("✅ Imagem criada por: " + nome)
            return data
        except Exception as e:
            log.append("❌ Imagem " + nome + ": " + str(e)[:160])
    return None


# ---- edição de foto (a IA recebe a foto de verdade) ----
def _edit_gemini(img, prompt):
    return _gemini_image_call([{"text": prompt}, {"inline_data": {"mime_type": _mime_img(img), "data": base64.b64encode(img).decode()}}])


def _edit_hf(img, prompt):
    if not _secret("HUGGINGFACE_API_KEY"):
        raise RuntimeError("sem chave")
    modelos = [m for m in [_secret("HF_EDIT_MODEL")] if m] + ["black-forest-labs/FLUX.1-Kontext-dev", "Qwen/Qwen-Image-Edit"]
    client = _hf_client(180)
    erros = []
    for model in modelos:
        try:
            return _to_bytes(client.image_to_image(img, prompt=prompt, model=model))
        except Exception as e:
            erros.append(model.split("/")[-1] + ": " + str(e)[:80])
    raise RuntimeError(" | ".join(erros)[:300])


def _edit_deepinfra(img, prompt):
    key = _secret("DEEPINFRA_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    model = _secret("DEEPINFRA_EDIT_MODEL") or "black-forest-labs/FLUX.1-Kontext-dev"
    r = requests.post("https://api.deepinfra.com/v1/images/edits",
                      headers={"Authorization": "Bearer " + key},
                      files={"image": ("foto.jpg", img, _mime_img(img))},
                      data={"prompt": prompt, "model": model, "n": "1", "response_format": "b64_json"}, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    d = r.json()["data"][0]
    if d.get("b64_json"):
        return base64.b64decode(d["b64_json"])
    if d.get("url"):
        return requests.get(d["url"], timeout=60).content
    raise RuntimeError("DeepInfra não devolveu imagem")


EDIT_CASCADE = [("DeepInfra (edita a sua foto)", _edit_deepinfra), ("OpenRouter (edita a sua foto)", _edit_openrouter),
                ("Gemini (edita a sua foto)", _edit_gemini),
                ("Hugging Face (edita a sua foto)", _edit_hf)]


def editar_imagem(img, prompt_edicao, descricao, log):
    """Tenta editar a foto original. Se nenhuma IA conseguir, recria a partir da descrição.
    Retorna (bytes|None, 'editada'|'recriada'|'')."""
    for nome, fn in EDIT_CASCADE:
        try:
            out = fn(img, prompt_edicao)
            log.append("✅ Foto editada por: " + nome)
            return out, "editada"
        except Exception as e:
            log.append("❌ " + nome + ": " + str(e)[:160])
    log.append("↪️ Nenhuma IA editou a foto original; recriando a partir da descrição")
    out = make_image((descricao + ". " + prompt_edicao)[:900], log)
    return out, ("recriada" if out else "")


# ---- vídeo ----
def make_video(prompt, log, img=None):
    """Vídeo experimental via Hugging Face (foto→vídeo se houver foto; senão texto→vídeo)."""
    if not _secret("HUGGINGFACE_API_KEY"):
        log.append("⚪ Vídeo: sem HUGGINGFACE_API_KEY")
        return None
    try:
        client = _hf_client(600)
    except ImportError:
        log.append("❌ Vídeo: falta huggingface_hub no requirements.txt")
        return None
    if img is not None and hasattr(client, "image_to_video"):
        try:
            model = _secret("HF_I2V_MODEL") or "Wan-AI/Wan2.2-I2V-A14B"
            out = client.image_to_video(img, prompt=prompt, model=model)
            log.append("✅ Vídeo (foto→vídeo): " + model)
            return _to_bytes(out)
        except Exception as e:
            log.append("❌ Vídeo foto→vídeo: " + str(e)[:160])
    try:
        model = _secret("HF_VIDEO_MODEL") or "Wan-AI/Wan2.1-T2V-1.3B"
        out = client.text_to_video(prompt, model=model)
        log.append("✅ Vídeo (texto→vídeo): " + model)
        return _to_bytes(out)
    except Exception as e:
        log.append("❌ Vídeo texto→vídeo: " + str(e)[:160] + " (modelo pode exigir plano pago; troque com HF_VIDEO_MODEL)")
    return None


# ============================================================
# PIPELINE MULTI-IA (só texto): busca web -> resumo -> prompt -> mídia
# ============================================================
def _llm(prompt, system, log, etapa):
    ok, texto, prov, erros = get_llm_response(
        st.session_state.session_id, prompt, st.session_state.get("model_sel", "groq"),
        system=system, use_history=False)
    if ok:
        extra = " (fallback: " + "; ".join(erros) + ")" if erros else ""
        log.append("✅ " + etapa + ": " + str(prov) + extra)
        return texto
    log.append("❌ " + etapa + ": " + texto[:150])
    return ""


def run_pipeline(pedido, quer_img, quer_vid):
    log = []
    contexto, fonte = web_search(pedido)
    log.append("🔎 Busca (" + fonte + "): ok" if contexto else "⚪ Busca: sem resultados, seguindo sem dados da web")
    resumo = ""
    if contexto:
        resumo = _llm("Pedido: " + pedido + "\n\nContexto da web:\n" + contexto[:4000],
                      "Você é um pesquisador. Resuma em português, em até 8 linhas objetivas, os fatos úteis do contexto para atender ao pedido. Não invente nada.",
                      log, "Resumo da pesquisa")
    sys_b = ("Você é engenheiro de prompts para IAs de mídia. Responda EXATAMENTE em 2 linhas, sem mais nada:\n"
             "IMAGEM: <prompt detalhado em inglês: sujeito, estilo, iluminação, composição, cores, câmera>\n"
             "VIDEO: <prompt em inglês descrevendo ação e movimento de câmera em 5 segundos>")
    bruto = _llm("Pedido: " + pedido + "\n\nFatos:\n" + resumo, sys_b, log, "Prompt otimizado")
    m_img = re.search(r"IMAGEM:\s*(.+)", bruto or "")
    m_vid = re.search(r"VIDEO:\s*(.+)", bruto or "")
    prompt_img = m_img.group(1).strip() if m_img else pedido
    prompt_vid = m_vid.group(1).strip() if m_vid else prompt_img
    img = make_image(prompt_img, log) if quer_img else None
    vid = make_video(prompt_vid, log) if quer_vid else None
    return {"log": log, "resumo": resumo, "prompt_img": prompt_img, "prompt_vid": prompt_vid, "img": img, "vid": vid}


def render_pipeline_ui():
    with st.expander("🔗 Pipeline Multi-IA (busca → prompt otimizado → imagem/vídeo)", expanded=False):
        pedido = st.text_area("O que você quer criar?", key="pipe_req", placeholder="Ex.: um dragão de cristal sobre uma cidade futurista em 2026")
        c1, c2 = st.columns(2)
        quer_img = c1.checkbox("🖼️ Gerar imagem", value=True, key="pipe_img")
        quer_vid = c2.checkbox("🎬 Gerar vídeo (experimental)", value=False, key="pipe_vid")
        if st.button("🚀 Executar pipeline", key="pipe_run") and pedido.strip():
            with st.spinner("Encadeando IAs..."):
                st.session_state.pipe_res = run_pipeline(pedido.strip(), quer_img, quer_vid)
        res = st.session_state.get("pipe_res")
        if res:
            if res["resumo"]:
                st.markdown("**Resumo da pesquisa**")
                st.write(res["resumo"])
            st.markdown("**Prompt otimizado (imagem)**")
            st.code(res["prompt_img"], language="text")
            if res["img"]:
                st.image(res["img"], caption="Imagem gerada")
            if res["vid"]:
                st.video(res["vid"])
            with st.expander("Log do pipeline e fallbacks", expanded=not (res["img"] or res["vid"])):
                st.write("\n\n".join(res["log"]))


# ============================================================
# VISÃO: a IA enxerga a foto (cascata entre provedores com visão)
# ============================================================
def _vis_openai(url, key, model, img_b64, mime, prompt, extra=None):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    r = requests.post(url, headers=headers, json={
        "model": model, "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + img_b64}}]}]}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _vis_groq(k, b, m, p):
    return _vis_openai("https://api.groq.com/openai/v1/chat/completions", k,
                       _secret("GROQ_VISION_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct", b, m, p)


def _gemini_generate(key, payload, modelos=None):
    modelos = modelos or ([x for x in [_secret("GEMINI_VISION_MODEL")] if x] + ["gemini-2.5-flash", "gemini-flash-latest"])
    ultimo = ""
    for model in modelos:
        for ver in ("v1", "v1beta"):
            r = requests.post(f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
                              headers={"Content-Type": "application/json", "x-goog-api-key": key}, json=payload, timeout=90)
            if r.status_code == 200:
                return "".join(x.get("text", "") for x in r.json()["candidates"][0]["content"]["parts"]).strip()
            ultimo = f"({r.status_code}) {r.text[:90]}"
            if r.status_code in (401, 403, 429):
                raise RuntimeError(ultimo)
    raise RuntimeError(ultimo)


def analisar_imagem(img_b64, mime, pergunta):
    """Visão do Gemini: texto + inline_data (base64). Retorna o texto ou uma mensagem de erro."""
    key = _secret("GEMINI_API_KEY")
    if not key:
        return "❌ GEMINI_API_KEY ausente."
    try:
        return _gemini_generate(key, {"contents": [{"role": "user", "parts": [
            {"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]})
    except Exception as e:
        return "❌ Erro na análise: " + str(e)[:200]


def _vis_gemini(k, b, m, p):
    return _gemini_generate(k, {"contents": [{"role": "user", "parts": [
        {"text": p}, {"inline_data": {"mime_type": m, "data": b}}]}]})


def _vis_claude(k, b, m, p):
    model = _secret("ANTHROPIC_MODEL") or "claude-haiku-5-5"
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": k, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                      json={"model": model, "max_tokens": 900, "messages": [{"role": "user", "content": [
                          {"type": "image", "source": {"type": "base64", "media_type": m, "data": b}},
                          {"type": "text", "text": p}]}]}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return "".join(x.get("text", "") for x in r.json()["content"]).strip()


def _vis_hf(k, b, m, p):
    return _vis_openai("https://router.huggingface.co/v1/chat/completions", k,
                       _secret("HF_VISION_MODEL") or "Qwen/Qwen2.5-VL-7B-Instruct", b, m, p)


def _vis_github(k, b, m, p):
    return _vis_openai("https://models.github.ai/inference/chat/completions", k,
                       _secret("GITHUB_VISION_MODEL") or "openai/gpt-4.1-mini", b, m, p,
                       extra={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})


def _vis_deepinfra(k, b, m, p):
    modelos = [x for x in [_secret("DEEPINFRA_VISION_MODEL")] if x] + ["Qwen/Qwen2.5-VL-32B-Instruct", "meta-llama/Llama-3.2-11B-Vision-Instruct"]
    ultimo = None
    for model in modelos:
        try:
            return _vis_openai("https://api.deepinfra.com/v1/openai/chat/completions", k, model, b, m, p)
        except Exception as e:
            ultimo = e
    raise ultimo


VISION_CASCADE = {"deepinfra": _vis_deepinfra, "openrouter": _vis_openrouter, "groq": _vis_groq, "gemini": _vis_gemini, "claude": _vis_claude, "huggingface": _vis_hf, "github": _vis_github}


def ask_vision(img_bytes, comando):
    """Retorna (texto, provedor, log). Começa pelo modelo escolhido e cai em cascata."""
    img_b64, mime = base64.b64encode(img_bytes).decode(), _mime_img(img_bytes)
    escolhido = st.session_state.get("model_sel", "groq")
    ordem = []
    for p_ in ["gemini", escolhido] + PROVIDERS:
        if p_ not in ordem:
            ordem.append(p_)
    log = []
    for prov in ordem:
        if prov not in VISION_CASCADE:
            continue
        key = _secret(KEYS[prov])
        if not key:
            log.append("⚪ Visão " + prov + ": sem chave")
            continue
        try:
            txt = VISION_CASCADE[prov](key, img_b64, mime, comando)
            if txt:
                log.append("✅ Visão: " + prov + " enxergou a foto")
                return txt, prov, log
        except Exception as e:
            log.append("❌ Visão " + prov + ": " + str(e)[:120])
    return None, None, log


# ============================================================
# ESTÚDIO: as IAs conversam entre si
# foto -> (IA de visão descreve) -> (IA de texto cria o comando)
# -> (IA de imagem edita a foto) -> (IA de vídeo anima)
# ============================================================
ACOES = ["Descrever", "Editar foto", "Criar vídeo", "Editar foto + vídeo"]
ESTILOS_FOTO = {
    "Automático": "",
    "Realista HD": "ultra realistic, sharp details, natural skin texture, professional photography",
    "Cyberpunk neon": "cyberpunk neon lights, futuristic city glow, magenta and cyan rim light",
    "Anime": "anime style, vibrant colors, clean line art",
    "Pintura a óleo": "classic oil painting, visible brush strokes, rich textures",
    "Retrato de estúdio": "professional studio portrait, soft key light, shallow depth of field",
    "Preto e branco cinematográfico": "cinematic black and white, high contrast, film grain",
    "Fantasia épica": "epic fantasy art, dramatic lighting, magical atmosphere",
    "Cartoon 3D": "3D cartoon render, soft shading, vivid colors",
    "Aquarela": "delicate watercolor painting, soft bleeding colors, paper texture",
}
MOVIMENTOS_VIDEO = {
    "Automático": "",
    "Zoom lento (aproximar)": "slow zoom in",
    "Panorâmica lateral": "smooth horizontal pan",
    "Câmera orbitando": "slow orbiting camera around the subject",
    "Câmera na mão (realista)": "natural handheld camera shake",
    "Animação sutil (respirar e piscar)": "subtle natural animation, breathing, blinking, hair moving in the wind",
    "Cinematográfico dramático": "dramatic cinematic push-in with moving light",
}


def executar_estudio(img, comando, acao, estilo="Automático", movimento="Automático"):
    log = []
    extra_estilo, extra_mov = ESTILOS_FOTO.get(estilo, ""), MOVIMENTOS_VIDEO.get(movimento, "")
    res = {"orig": img, "acao": acao, "desc": "", "prompt_edit": "", "prompt_vid": "", "img": None, "modo_img": "", "vid": None, "log": log}
    if acao == "Descrever":
        txt, prov, vlog = ask_vision(img, comando or "Descreva o que você vê.")
        log.extend(vlog)
        res["desc"] = txt or ""
        return res
    # 1) visão: a IA enxerga a foto
    desc, prov, vlog = ask_vision(img, "Describe this photo in detail in English (people, clothing, pose, background, colors, lighting, framing). Max 8 lines.")
    log.extend(vlog)
    res["desc"] = desc or ""
    # 2) IA de texto transforma pedido + descrição em comandos para as IAs de mídia
    sys_p = ("You are a prompt engineer for image-editing and video AIs. Reply EXACTLY 2 lines, nothing else:\n"
             "EDIT: <instruction to edit the ORIGINAL photo as requested, keeping the person's identity, pose and composition>\n"
             "VIDEO: <5-second motion prompt: subtle animation and camera movement for this photo>")
    bruto = _llm("Photo description:\n" + (desc or "(unavailable)") + "\n\nUser request (Portuguese): " + (comando or "melhore esta foto")
                 + ("\n\nRequired visual style: " + extra_estilo if extra_estilo else "")
                 + ("\n\nCamera motion for the video: " + extra_mov if extra_mov else ""),
                 sys_p, log, "Comando para as IAs de imagem")
    m_e = re.search(r"EDIT:\s*(.+)", bruto or "")
    m_v = re.search(r"VIDEO:\s*(.+)", bruto or "")
    res["prompt_edit"] = m_e.group(1).strip() if m_e else ((comando or "enhance this photo, keep identity") + (", " + extra_estilo if extra_estilo else ""))
    res["prompt_vid"] = m_v.group(1).strip() if m_v else res["prompt_edit"]
    # 3) IA de imagem recebe a foto + comando
    if acao in ("Editar foto", "Editar foto + vídeo"):
        res["img"], res["modo_img"] = editar_imagem(img, res["prompt_edit"], desc or "", log)
    # 4) IA de vídeo anima a foto (original ou editada)
    if acao in ("Criar vídeo", "Editar foto + vídeo"):
        res["vid"] = make_video(res["prompt_vid"], log, res["img"] or img)
    return res


def mostrar_estudio_res(res):
    if res.get("desc"):
        st.markdown("**👁️ O que a IA viu / respondeu**")
        st.write(res["desc"])
    if res.get("prompt_edit"):
        st.markdown("**Comando enviado às IAs de imagem**")
        st.code(res["prompt_edit"], language="text")
    if res.get("img"):
        c1, c2 = st.columns(2)
        c1.image(res["orig"], caption="Original")
        legenda = "Foto editada" if res["modo_img"] == "editada" else "Recriada a partir da descrição (nenhuma IA conseguiu editar a original)"
        c2.image(res["img"], caption=legenda)
        c2.download_button("⬇️ Baixar foto", res["img"], file_name="zeus_foto.png", mime="image/png", key="dl_estudio_img")
    if res.get("vid"):
        st.video(res["vid"])
        st.download_button("⬇️ Baixar vídeo", res["vid"], file_name="zeus_video.mp4", mime="video/mp4", key="dl_estudio_vid")
    pediu_midia = res.get("acao") != "Descrever"
    falhou = pediu_midia and not (res.get("img") or res.get("vid"))
    with st.expander("Log das IAs (erros e fallbacks)", expanded=falhou):
        st.write("\n\n".join(res["log"]))


def render_estudio_ui():
    tem = bool(st.session_state.get("estudio_res"))
    with st.expander("🎨 Estúdio Multi-IA (foto → editar → vídeo)", expanded=tem):
        up = st.file_uploader("📁 Enviar uma foto (ou use a câmera do menu lateral)", type=["jpg", "jpeg", "png", "webp"], key="estudio_up")
        if up is not None:
            dados = up.getvalue()
            h = hashlib.md5(dados).hexdigest()
            if h != st.session_state.get("up_last"):
                st.session_state.up_last = h
                st.session_state.foto_bytes = dados
        foto = st.session_state.get("foto_bytes")
        if foto:
            st.image(foto, caption="Foto ativa", width=220)
        else:
            st.info("Tire uma foto na câmera lateral ou envie uma aqui.")
        comando = st.text_input("O que fazer com a foto?", value="Melhore esta foto deixando-a mais nítida e com visual cyberpunk", key="estudio_cmd")
        acao = st.radio("Ação", ACOES, index=1, key="estudio_acao", horizontal=True)
        col_a, col_b = st.columns(2)
        estilo = col_a.selectbox("🎨 Estilo da foto", list(ESTILOS_FOTO.keys()), key="estudio_estilo")
        movimento = col_b.selectbox("🎬 Movimento do vídeo", list(MOVIMENTOS_VIDEO.keys()), key="estudio_mov")
        if st.button("🚀 Executar", key="estudio_run", disabled=not foto):
            with st.spinner("As IAs estão conversando entre si..."):
                st.session_state.estudio_res = executar_estudio(foto, comando, acao, estilo, movimento)
            st.rerun()
        res = st.session_state.get("estudio_res")
        if res:
            mostrar_estudio_res(res)


def render_camera_sidebar():
    st.subheader("📷 Câmera direta")
    foto = st.camera_input("Tirar foto", key="cam_foto", label_visibility="collapsed")
    comando = st.text_input("Comando para a foto", value="Descreva o que você vê.", key="cam_cmd")
    acao = st.selectbox("Ação", ACOES, key="cam_acao")
    if foto is not None:
        dados = foto.getvalue()
        h = hashlib.md5(dados).hexdigest()
        nova = h != st.session_state.get("cam_last")
        if nova:
            st.session_state.foto_bytes = dados
        if nova or st.button("▶ Rodar de novo", key="cam_run", use_container_width=True):
            st.session_state.cam_last = h
            with st.spinner("As IAs estão trabalhando na foto..."):
                res = executar_estudio(dados, comando, acao)
            st.session_state.estudio_res = res
            if acao == "Descrever" and res.get("desc"):
                sid = st.session_state.session_id
                st.session_state.messages.append({"role": "user", "content": "📷 " + comando})
                st.session_state.messages.append({"role": "assistant", "content": res["desc"]})
                add_message(sid, "user", "📷 " + comando)
                add_message(sid, "assistant", res["desc"])
    res = st.session_state.get("estudio_res")
    if res:
        if res.get("desc") and res.get("acao") == "Descrever":
            st.write(res["desc"])
        st.caption("✅ Resultado e log no 🎨 Estúdio Multi-IA, na página principal.")


def render_visao_gerar_sidebar():
    st.subheader("👁️ Analisar imagem")
    arq = st.file_uploader("Enviar imagem", type=["png", "jpg", "jpeg", "webp"], key="side_up")
    if arq is not None:
        dados = arq.getvalue()
        h = hashlib.md5(dados).hexdigest()
        if h != st.session_state.get("side_up_last"):
            st.session_state.side_up_last = h
            st.session_state.foto_bytes = dados  # também vira a foto ativa do Estúdio
        st.image(dados, use_container_width=True)
        pergunta = st.text_input("Pergunta sobre a imagem", value="Descreva esta imagem.", key="side_q")
        if st.button("🔍 Analisar imagem", key="side_analisar", use_container_width=True):
            with st.spinner("Analisando..."):
                resp = analisar_imagem(base64.b64encode(dados).decode("utf-8"), _mime_img(dados), pergunta)
            st.session_state.side_analise = resp
            sid = st.session_state.session_id
            st.session_state.messages.append({"role": "user", "content": "🖼️ " + pergunta})
            st.session_state.messages.append({"role": "assistant", "content": resp})
            add_message(sid, "user", "🖼️ " + pergunta)
            add_message(sid, "assistant", resp)
    if st.session_state.get("side_analise"):
        st.write(st.session_state.side_analise)

    st.subheader("🎨 Gerar imagem")
    estilo_g = st.selectbox("Estilo", list(ESTILOS_FOTO.keys()), key="side_estilo")
    prompt_img = st.text_input("Prompt da imagem", key="side_prompt")
    if st.button("✨ Gerar", key="side_gerar", use_container_width=True) and prompt_img.strip():
        glog = []
        with st.spinner("Gerando imagem..."):
            img = make_image(prompt_img.strip() + (", " + ESTILOS_FOTO[estilo_g] if ESTILOS_FOTO.get(estilo_g) else ""), glog)
        st.session_state.side_img = (img, glog)
    resg = st.session_state.get("side_img")
    if resg:
        img, glog = resg
        if img:
            st.image(img)
            st.download_button("⬇️ Baixar imagem", img, file_name="zeus_imagem.png", mime="image/png", key="dl_side_img")
        else:
            st.error("Não consegui gerar a imagem. Veja o log.")
        with st.expander("Log da geração", expanded=not img):
            st.write("\n\n".join(glog))


FALLBACK_VOZES_EXTRA = {
    "Thalita multilíngue (pt-BR ♀)": "pt-BR-ThalitaMultilingualNeural",
    "Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural",
    "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural",
    "Raquel (pt-PT ♀)": "pt-PT-RaquelNeural",
    "Duarte (pt-PT ♂)": "pt-PT-DuarteNeural",
    "Ava multilíngue (en-US ♀)": "en-US-AvaMultilingualNeural",
    "Andrew multilíngue (en-US ♂)": "en-US-AndrewMultilingualNeural",
    "Emma multilíngue (en-US ♀)": "en-US-EmmaMultilingualNeural",
    "Brian multilíngue (en-US ♂)": "en-US-BrianMultilingualNeural",
    "Vivienne multilíngue (fr-FR ♀)": "fr-FR-VivienneMultilingualNeural",
    "Remy multilíngue (fr-FR ♂)": "fr-FR-RemyMultilingualNeural",
    "Seraphina multilíngue (de-DE ♀)": "de-DE-SeraphinaMultilingualNeural",
    "Florian multilíngue (de-DE ♂)": "de-DE-FlorianMultilingualNeural",
}


@st.cache_data(ttl=86400, show_spinner=False)
def listar_todas_vozes_edge():
    """Todas as vozes neurais do Edge (português primeiro). Multilíngues falam português com sotaque."""
    try:
        import edge_tts
        todas = asyncio.run(edge_tts.list_voices())
        ordenadas = sorted(todas, key=lambda x: (not x["ShortName"].startswith("pt-"), x["Locale"], x["ShortName"]))
        vozes = {}
        for v in ordenadas:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
            vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes or FALLBACK_VOZES_EXTRA
    except Exception:
        return FALLBACK_VOZES_EXTRA


# ============================================================
# AUTO-EVOLUÇÃO SEGURA + CRIADOR DE PASTAS (ZIP)
# Claude primeiro (3 tentativas, 60 s entre elas) -> Gemini (plano B)
# -> validação com ast (sintaxe + regras de segurança) -> GitHub.
# ============================================================
TRANSITORIOS = (429, 500, 502, 503, 504, 529)
SKILLS_IMPORTS_OK = {
    "math", "re", "json", "datetime", "random", "statistics", "collections", "itertools", "functools",
    "textwrap", "string", "unicodedata", "decimal", "fractions", "base64", "hashlib", "typing", "urllib",
    "requests", "time", "html", "uuid", "calendar", "operator", "heapq", "bisect", "copy", "dataclasses", "enum",
}
SKILLS_NOMES_PROIBIDOS = {
    "eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "exit", "quit", "memoryview",
}
SYS_HABILIDADE = (
    "Você é um engenheiro Python sênior. Escreva funções Python puras e seguras que implementem o pedido. "
    "Responda APENAS com código Python, sem markdown e sem explicações. Cada função deve ter uma docstring curta em português. "
    "Nada de código solto fora de funções: apenas imports permitidos, funções e constantes. "
    "Imports permitidos: " + ", ".join(sorted(SKILLS_IMPORTS_OK)) + ". "
    "Proibido: os, sys, subprocess, socket, shutil, pathlib, open(), eval(), exec(), __import__ e atributos que começam com dois underscores."
)


def _extrair_codigo(texto):
    """Remove cercas ``` e texto fora do código."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", texto, re.S)
    return (m.group(1) if m else texto).strip()


def validar_codigo_habilidade(codigo):
    """Valida a sintaxe com ast e aplica regras de segurança. Retorna (ok, mensagem)."""
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as e:
        return False, f"erro de sintaxe na linha {e.lineno}: {e.msg}"
    for no in arvore.body:
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(no, ast.Expr) and isinstance(getattr(no, "value", None), ast.Constant):
            continue  # docstring
        if (isinstance(no, ast.Assign) and all(isinstance(t, ast.Name) for t in no.targets)
                and isinstance(no.value, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set))):
            continue  # constantes simples
        return False, "só são permitidos imports, funções e constantes no nível principal"
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in arvore.body):
        return False, "nenhuma função encontrada no código"
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                if a.name.split(".")[0] not in SKILLS_IMPORTS_OK:
                    return False, "import não permitido: " + a.name
        elif isinstance(no, ast.ImportFrom):
            if no.level or (no.module or "").split(".")[0] not in SKILLS_IMPORTS_OK:
                return False, "import não permitido: " + str(no.module)
        elif isinstance(no, ast.Name) and no.id in SKILLS_NOMES_PROIBIDOS:
            return False, "uso proibido de: " + no.id
        elif isinstance(no, ast.Attribute) and no.attr.startswith("__"):
            return False, "acesso proibido a: " + no.attr
    return True, "ok"


def _claude_raw(sistema, pedido, max_tokens):
    """Uma chamada à API da Anthropic. Retorna (codigo_http, texto)."""
    key = _secret("ANTHROPIC_API_KEY")
    if not key:
        return 401, "ANTHROPIC_API_KEY ausente"
    candidatos = [m for m in [_secret("ANTHROPIC_MODEL")] if m] + MODELS["claude"]
    ultimo = (0, "")
    for modelo in candidatos:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": modelo, "max_tokens": max_tokens, "system": sistema,
                      "messages": [{"role": "user", "content": pedido}]},
                timeout=120)
        except Exception as e:
            return 503, "erro de conexão: " + str(e)[:100]
        if r.status_code == 200:
            return 200, "".join(b.get("text", "") for b in r.json()["content"])
        ultimo = (r.status_code, r.text[:200])
        if r.status_code not in (400, 404):  # 400/404 = modelo indisponível: tenta o próximo
            break
    return ultimo


def _gemini_plano_b(sistema, pedido):
    key = _secret("GEMINI_API_KEY")
    if not key:
        return None
    try:
        return _gemini_generate(key, {"contents": [{"role": "user", "parts": [{"text": sistema + "\n\n" + pedido}]}]},
                                modelos=MODELS["gemini"])
    except Exception:
        return None


def gerar_com_prioridade_claude(sistema, pedido, max_tokens=2500):
    """Claude PRIMEIRO. Se estiver ocupado (429/503...), entra na fila e tenta de novo
    (até 3 vezes, esperando 60 s). Depois, Gemini como plano B. Retorna (texto, provedor) ou (None, None)."""
    fila = st.session_state.setdefault("fila_habilidades", [])
    fila.append(pedido)
    aviso = st.empty()
    try:
        for tentativa in range(1, 4):
            code, texto = _claude_raw(sistema, pedido, max_tokens)
            if code == 200 and texto.strip():
                return texto, "Claude"
            if code not in TRANSITORIOS:
                break  # erro permanente (chave, créditos...): não adianta esperar
            aviso.warning("⏳ Claude ocupado. Entrando na fila de espera...")
            st.session_state.fila_habilidades = fila
            if tentativa < 3:
                st.caption(f"Pedidos na fila: {len(fila)} · tentativa {tentativa}/3 · nova tentativa em 60 s")
                time.sleep(60)
        aviso.info("🔁 Claude indisponível. Usando o Gemini como plano B...")
        texto = _gemini_plano_b(sistema, pedido)
        if texto and texto.strip():
            return texto, "Gemini"
        return None, None
    finally:
        if pedido in fila:
            fila.remove(pedido)
        aviso.empty()


def _recarregar_skills():
    try:
        if "zeus_skills" in sys.modules:
            importlib.reload(sys.modules["zeus_skills"])
        else:
            importlib.import_module("zeus_skills")
    except Exception:
        pass


def listar_habilidades():
    try:
        mod = importlib.import_module("zeus_skills")
        return {n: f for n, f in inspect.getmembers(mod, inspect.isfunction)
                if not n.startswith("_") and f.__module__ == mod.__name__}
    except Exception:
        return {}


def aprender_nova_habilidade(prompt_usuario):
    """Gera (Claude -> Gemini), valida com ast, salva no GitHub e recarrega o app."""
    texto, prov = gerar_com_prioridade_claude(SYS_HABILIDADE, "Pedido: " + prompt_usuario)
    if not texto:
        st.error("❌ Não foi possível gerar o código agora. Tente novamente mais tarde.")
        return False
    codigo = _extrair_codigo(texto)
    ok, msg = validar_codigo_habilidade(codigo)
    if not ok:
        st.error("❌ Código gerado foi recusado na validação: " + msg)
        with st.expander("Ver o código recusado"):
            st.code(codigo, language="python")
        return False
    st.session_state.ultima_habilidade = {"pedido": prompt_usuario, "codigo": codigo, "provedor": prov}
    if salvar_no_github is None:
        st.error("❌ github_manager.py não encontrado ao lado do app.py. Use a pasta completa do projeto.")
        return False
    ok2, msg2 = salvar_no_github(codigo, "Zeus: nova habilidade - " + prompt_usuario[:60])
    if not ok2:
        st.error("❌ Não consegui salvar no GitHub: " + msg2)
        return False
    _recarregar_skills()
    st.success("✅ Habilidade validada e salva! O app vai recarregar em 3 segundos.")
    time.sleep(3)
    st.rerun()


# ---------- criador de pastas (ZIP) ----------
SYS_PROJETO = (
    "Você é um engenheiro de software sênior. Gere um projeto completo em vários arquivos. "
    "Formato OBRIGATÓRIO: para cada arquivo escreva uma linha '### FILE: caminho/relativo.ext' seguida do conteúdo completo do arquivo, "
    "sem cercas de markdown e sem texto fora dos arquivos. Inclua requirements.txt e README.md. "
    "Todo código Python deve ser sintaticamente válido e pronto para rodar."
)


def _parse_arquivos(texto):
    partes = re.split(r"^### FILE:\s*(.+?)\s*$", texto, flags=re.M)
    arquivos, avisos = {}, []
    for i in range(1, len(partes) - 1, 2):
        nome = partes[i].strip().strip("`").replace("\\", "/")
        partes_nome = [p for p in nome.split("/") if p not in ("", ".")]
        if not partes_nome or nome.startswith("/") or ".." in partes_nome:
            avisos.append("Arquivo ignorado (caminho inseguro): " + nome)
            continue
        conteudo = partes[i + 1].strip("\n")
        conteudo = re.sub(r"^```[a-zA-Z]*\n", "", conteudo)
        conteudo = re.sub(r"\n```\s*$", "", conteudo)
        caminho = "/".join(partes_nome)
        if caminho.endswith(".py"):
            try:
                ast.parse(conteudo)
            except SyntaxError as e:
                avisos.append(f"{caminho}: erro de sintaxe na linha {e.lineno} ({e.msg})")
        arquivos[caminho] = conteudo.rstrip() + "\n"
    return arquivos, avisos


def montar_zip(pasta, arquivos):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho, conteudo in arquivos.items():
            z.writestr(pasta + "/" + caminho, conteudo)
    return buf.getvalue()


def criar_projeto_zip(descricao):
    """Gera uma PASTA de projeto (vários arquivos) e devolve em ZIP para download."""
    texto, prov = gerar_com_prioridade_claude(SYS_PROJETO, descricao, max_tokens=6000)
    if not texto:
        st.error("❌ Não foi possível gerar o código agora. Tente novamente mais tarde.")
        return None
    arquivos, avisos = _parse_arquivos(texto)
    if not arquivos:
        st.error("❌ A IA não devolveu arquivos no formato esperado. Tente reformular o pedido.")
        return None
    pasta = re.sub(r"[^a-z0-9]+", "_", descricao.lower()).strip("_")[:30] or "projeto_zeus"
    return {"pasta": pasta, "arquivos": arquivos, "avisos": avisos, "provedor": prov,
            "zip": montar_zip(pasta, arquivos)}


SECRETS_EXEMPLO = '''GROQ_API_KEY = "cole_aqui"
GEMINI_API_KEY = "cole_aqui"
ANTHROPIC_API_KEY = "cole_aqui"
HUGGINGFACE_API_KEY = "cole_aqui"
GITHUB_API_KEY = "cole_aqui"      # token com Contents: Read and write (e Models: read)
GITHUB_USER = "seu_usuario"
GITHUB_REPO = "seu_repositorio"
DEEPINFRA_API_KEY = "cole_aqui"
OPENROUTER_API_KEY = "cole_aqui"
'''


def exportar_zeus_zip():
    """ZIP do próprio Zeus (app.py, github_manager.py, zeus_skills.py, requirements.txt)."""
    base = os.path.dirname(os.path.abspath(__file__))
    arquivos = {}
    for nome in ("app.py", "github_manager.py", "zeus_skills.py", "requirements.txt"):
        try:
            with open(os.path.join(base, nome), encoding="utf-8") as f:
                arquivos[nome] = f.read()
        except Exception:
            pass
    arquivos[".streamlit/secrets.toml.example"] = SECRETS_EXEMPLO
    arquivos[".gitignore"] = ".streamlit/secrets.toml\n__pycache__/\n*.db\n"
    return montar_zip("zeus_projeto", arquivos)


def _chamar_habilidade(fn, texto):
    if not texto.strip():
        return fn()
    try:
        val = json.loads(texto)
    except Exception:
        return fn(texto)
    if isinstance(val, list):
        return fn(*val)
    if isinstance(val, dict):
        return fn(**val)
    return fn(val)


def render_evolucao_ui():
    with st.expander("🧬 Auto-Evolução e Criador de Pastas (ZIP)", expanded=False):
        aba1, aba2, aba3 = st.tabs(["🧬 Nova habilidade", "📦 Criar projeto (ZIP)", "📥 Baixar o Zeus"])
        with aba1:
            pedido = st.text_area("O que o Zeus deve aprender a fazer?", key="evo_pedido",
                                  placeholder="Ex.: uma função que converte reais para dólar dado o valor e a cotação")
            if st.button("🧬 Aprender habilidade", key="evo_run") and pedido.strip():
                aprender_nova_habilidade(pedido.strip())
            ultima = st.session_state.get("ultima_habilidade")
            if ultima:
                st.caption("Última habilidade gerada por " + str(ultima["provedor"]) + ": " + ultima["pedido"][:80])
                st.code(ultima["codigo"], language="python")
                st.download_button("⬇️ Baixar pasta da habilidade (ZIP)",
                                   montar_zip("zeus_habilidade", {"zeus_skills.py": ultima["codigo"] + "\n"}),
                                   file_name="zeus_habilidade.zip", mime="application/zip", key="evo_dl_skill")
            skills = listar_habilidades()
            if skills:
                st.markdown("**Habilidades instaladas**")
                nome = st.selectbox("Escolha", list(skills.keys()), key="evo_skill_sel")
                st.caption((inspect.getdoc(skills[nome]) or "sem descrição") + " · " + str(inspect.signature(skills[nome])))
                args = st.text_input("Argumentos (texto simples, lista JSON ou objeto JSON)", key="evo_args")
                if st.button("▶ Executar habilidade", key="evo_exec"):
                    try:
                        st.write(_chamar_habilidade(skills[nome], args))
                    except Exception as e:
                        st.error("Erro ao executar: " + str(e)[:200])
            if st.session_state.get("fila_habilidades"):
                st.caption("Fila de espera: " + str(len(st.session_state.fila_habilidades)) + " pedido(s)")
        with aba2:
            desc = st.text_area("Descreva o projeto que o Zeus deve criar", key="proj_desc",
                                placeholder="Ex.: app Streamlit de lista de tarefas com SQLite, com requirements e README")
            if st.button("📦 Gerar pasta do projeto", key="proj_run") and desc.strip():
                with st.spinner("Criando a pasta do projeto..."):
                    st.session_state.projeto_zip = criar_projeto_zip(desc.strip())
            proj = st.session_state.get("projeto_zip")
            if proj:
                st.success(f"Pasta '{proj['pasta']}' criada por {proj['provedor']} com {len(proj['arquivos'])} arquivo(s).")
                for a in proj["avisos"]:
                    st.warning(a)
                st.download_button("⬇️ Baixar a pasta (ZIP)", proj["zip"], file_name=proj["pasta"] + ".zip",
                                   mime="application/zip", key="proj_dl")
                ver = st.selectbox("Pré-visualizar arquivo", list(proj["arquivos"].keys()), key="proj_ver")
                st.code(proj["arquivos"][ver], language=("python" if ver.endswith(".py") else None))
        with aba3:
            st.write("Baixe o Zeus inteiro como pasta pronta para subir no GitHub.")
            st.download_button("⬇️ Baixar zeus_projeto.zip", exportar_zeus_zip(), file_name="zeus_projeto.zip",
                               mime="application/zip", key="zeus_dl")



def testar_chaves():
    linhas = []
    msgs = [{"role": "user", "content": "Responda apenas: OK"}]
    for prov in PROVIDERS:
        key = _secret(KEYS[prov])
        if not key:
            linhas.append("⚪ " + prov + ": chave ausente ou placeholder")
            continue
        override = _secret(MODEL_SECRET[prov])
        try:
            ok, texto = _try_models([override] if override else MODELS[prov], lambda m: CALLERS[prov](key, m, msgs))
        except Exception as e:
            ok, texto = False, "erro de conexão: " + str(e)
        linhas.append(("✅ " + prov + ": funcionando") if ok else ("❌ " + prov + ": " + _resumo(texto)))
    return linhas


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

const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
let avatar = "sphere", SILENCE = 2, STT = "whisper", TTS = "edge", TTS_RATE = 1;
let initialized = false, lastReplyId = 0, pendingEl = null, pendingUserEl = null;
let state = "idle", pulse = 0;
let micOn = false, pausedForBot = false, buffer = "", silenceTimer = null, silenceDeadline = 0, thinkTimer = null, lastAfter = 0;
let rec = null, mediaRec = null;
const TITLES = { sphere: "[ CYBER-SPHERE v3.0 ]", eye: "[ CYBER-EYE v3.0 ]", robot: "[ CYBER-BOT v3.0 ]" };
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

// ================= Chat =================
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

// ================= Voz de saída =================
// Celulares só liberam áudio depois de um toque do usuário: "destrava" no primeiro toque.
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
  vs = pt.concat(vs.filter(function (v) { return !/^pt/i.test(v.lang); }));
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
  // Se o celular bloquear a fala em silêncio, não deixa o Zeus travado em "FALANDO"
  setTimeout(function () { if (!started && state === "speaking") { window.speechSynthesis.cancel(); afterSpeak(); } }, 4000);
}
function afterSpeak() {
  const n = Date.now(); if (n - lastAfter < 600) return; lastAfter = n;
  setState("idle"); pausedForBot = false;
  if (!micOn) return;
  if (STT === "whisper") setTimeout(function () { if (micOn && stream) beginRecording(); }, 300);
  else setTimeout(function () { try { rec.start(); } catch (e) {} }, 400);
}

// ================= Entrada A: Whisper (grava, detecta silêncio e envia) =================
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
    if (dur < 350) { pausedForBot = false; setState("idle"); beginRecording(); return; }  // só ruído
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

// ================= Entrada B: reconhecimento do navegador =================
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

// ================= Botão do microfone =================
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

// ---- helpers de render realista ----
function lg(x0, y0, x1, y1, stops) { const g = ctx.createLinearGradient(x0, y0, x1, y1); stops.forEach(function (s) { g.addColorStop(s[0], s[1]); }); return g; }
function rg(x, y, r0, r1, stops) { const g = ctx.createRadialGradient(x, y, r0, x, y, r1); stops.forEach(function (s) { g.addColorStop(s[0], s[1]); }); return g; }
function bolt(x, y, r) {
  ctx.fillStyle = rg(x - r * 0.3, y - r * 0.3, 0, r * 1.2, [[0, "#f6faff"], [0.6, "#8d9bb4"], [1, "#2b3347"]]);
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.65)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x - r * 0.6, y - r * 0.2); ctx.lineTo(x + r * 0.6, y + r * 0.2); ctx.stroke();
}

// ---- interação: mouse/toque seguem o avatar; clique troca a cor e "assusta" ----
const PAL = ["#00e5ff", "#ff2bd6", "#3dffb0", "#ffb02e", "#8b5cff"];
const HUES = [190, 315, 150, 38, 265];
let palIdx = 0, startle = 0, ripples = [];
const ptr = { x: 0, y: 0, active: false, last: 0 };
function ptrXY(e) { const r = (avatar === "sphere" ? document.getElementById("three-box") : cv).getBoundingClientRect(); ptr.x = e.clientX - r.left; ptr.y = e.clientY - r.top; ptr.active = true; ptr.last = performance.now(); }
document.addEventListener("pointermove", ptrXY);
document.addEventListener("pointerdown", function (e) {
  ptrXY(e);
  const tg = e.target && e.target.tagName;
  if (tg === "INPUT" || tg === "BUTTON" || tg === "SELECT") return;
  palIdx = (palIdx + 1) % PAL.length; startle = 1; ripples.push({ x: ptr.x, y: ptr.y, t0: performance.now() });
});
document.documentElement.addEventListener("pointerleave", function () { ptr.active = false; });
function accent() { return state === "listening" ? "#ff2bd6" : state === "thinking" ? "#ffb02e" : state === "speaking" ? "#3dffb0" : PAL[palIdx]; }
function trackPointer(cx, cy, range) {
  if (ptr.active && performance.now() - ptr.last > 4000) ptr.active = false;
  if (!ptr.active || state === "thinking") return 99999;
  const dx = (ptr.x - cx) / range, dy = (ptr.y - cy) / range;
  look.x += (Math.max(-1, Math.min(1, dx)) - look.x) * 0.22;
  look.y += (Math.max(-1, Math.min(1, dy)) - look.y) * 0.22;
  return Math.hypot(ptr.x - cx, ptr.y - cy);
}
function drawPointerFx(t, ac) {
  const now = performance.now();
  ripples = ripples.filter(function (r) { return now - r.t0 < 800; });
  ripples.forEach(function (r) {
    const a = (now - r.t0) / 800;
    ctx.strokeStyle = ac; ctx.globalAlpha = 1 - a; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(r.x, r.y, 10 + a * 90, 0, 7); ctx.stroke(); ctx.globalAlpha = 1;
  });
  if (ptr.active) {
    const px = ptr.x, py = ptr.y, s = 14 + 3 * Math.sin(t * 8);
    ctx.strokeStyle = ac; ctx.lineWidth = 1.5; glow(ac, 8);
    ctx.beginPath(); ctx.arc(px, py, s, 0, 7);
    ctx.moveTo(px - s - 6, py); ctx.lineTo(px - s + 4, py); ctx.moveTo(px + s - 4, py); ctx.lineTo(px + s + 6, py);
    ctx.moveTo(px, py - s - 6); ctx.lineTo(px, py - s + 4); ctx.moveTo(px, py + s - 4); ctx.lineTo(px, py + s + 6); ctx.stroke(); noGlow();
  }
  ctx.font = "10px 'Courier New', monospace"; ctx.fillStyle = "rgba(255,255,255,0.45)"; ctx.textAlign = "right";
  ctx.fillText("mova o mouse • clique: muda a cor", W - 10, 56); ctx.textAlign = "left";
}

// ---- OLHO CIBERNÉTICO ----
function drawEye(t) {
  const cx = W / 2, cy = H * 0.36, R = Math.min(W * 0.26, H * 0.17), rad = Math.PI / 180, ac = accent(), hue = HUES[palIdx];
  const ls = state === "listening", th = state === "thinking";
  const dist = trackPointer(cx, cy, R * 3.2);
  const near = ptr.active ? Math.max(0, 1 - dist / (R * 4)) : 0, hov = dist < R * 1.8 ? 1 : 0;
  startle = Math.max(0, startle - 0.025);
  ctx.fillStyle = rg(cx, cy, R * 0.9, R * 2.5, [[0, "hsla(" + hue + ",100%,55%,0.30)"], [0.5, "hsla(" + (hue + 90) + ",100%,60%,0.12)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 2.5, 0, 7); ctx.fill();
  // carcaça metálica com reflexo e rebites
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.85)"; ctx.shadowBlur = 26; ctx.shadowOffsetY = 10;
  ctx.fillStyle = lg(cx - R * 1.7, cy - R * 1.7, cx + R * 1.7, cy + R * 1.7, [[0, "#f2f7ff"], [0.22, "#93a4bf"], [0.5, "#2b354b"], [0.78, "#0e1321"], [1, "#6a7894"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.72, 0, 7); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.5)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, R * 1.69, Math.PI * 0.95, Math.PI * 1.7); ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
  for (let i = 0; i < 26; i++) { ctx.beginPath(); ctx.arc(cx, cy, R * (1.52 + i * 0.007), 0, 7); ctx.stroke(); }
  for (let i = 0; i < 8; i++) { const a = (i * 45 + 22.5) * rad; bolt(cx + Math.cos(a) * R * 1.6, cy + Math.sin(a) * R * 1.6, R * 0.05); }
  ctx.fillStyle = lg(cx, cy - R * 1.5, cx, cy + R * 1.5, [[0, "#03050a"], [1, "#182034"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.5, 0, 7); ctx.fill();
  // anel de LEDs (acende mais ao passar o mouse)
  const span = (th ? 130 : ls ? 220 : 90) + hov * 90 + startle * 120;
  for (let i = 0; i < 48; i++) {
    const a0 = i * 7.5, lit = (((a0 - ring) % 360) + 360) % 360 < span;
    ctx.strokeStyle = lit ? "hsl(" + ((hue + i * 3) % 360) + ",100%,60%)" : "rgba(255,255,255,0.07)"; ctx.lineWidth = R * (0.07 + hov * 0.02);
    if (lit) glow(ac, 9);
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.38, a0 * rad, (a0 + 5) * rad); ctx.stroke(); noGlow();
  }
  // lente + íris
  const LR = R * 1.2;
  ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.clip();
  ctx.fillStyle = rg(cx, cy, 0, LR, [[0, "#0a1a2e"], [1, "#010308"]]); ctx.fillRect(cx - LR, cy - LR, LR * 2, LR * 2);
  const ix = cx + look.x * R * 0.34, iy = cy + look.y * R * 0.24, ir = R * 0.88 * (1 + hov * 0.03), pr = ir * 0.34 * pupil * (1 - 0.3 * near) * (1 + 0.45 * startle);
  ctx.fillStyle = rg(ix, iy, 0, ir, [[0, "#02121f"], [0.3, "hsl(" + hue + ",100%,45%)"], [0.62, "hsl(" + (hue + 50) + ",100%,55%)"], [0.88, "hsl(" + (hue + 110) + ",100%,45%)"], [1, "#07101f"]]);
  ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.fill();
  for (let i = 0; i < 170; i++) {
    const a = i * 2.399963, r0 = pr * 1.05, r1 = ir * (0.72 + ((i * 53) % 28) / 100);
    ctx.strokeStyle = "hsla(" + (hue + (i * 37) % 90 - 25) + ",100%," + (45 + (i * 13) % 28) + "%,0.4)"; ctx.lineWidth = 1.1;
    ctx.beginPath(); ctx.moveTo(ix + r0 * Math.cos(a), iy + r0 * Math.sin(a)); ctx.lineTo(ix + r1 * Math.cos(a), iy + r1 * Math.sin(a)); ctx.stroke();
  }
  ctx.strokeStyle = "hsla(" + (hue + 40) + ",100%,72%,0.55)"; ctx.lineWidth = 2; ctx.beginPath();
  for (let i = 0; i <= 72; i++) { const a = i * 5 * rad, rr2 = ir * (0.5 + 0.025 * Math.sin(i * 2.7 + t)); const px = ix + rr2 * Math.cos(a), py = iy + rr2 * Math.sin(a); if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
  ctx.closePath(); ctx.stroke();
  ctx.strokeStyle = "rgba(0,0,12,0.85)"; ctx.lineWidth = R * 0.09; ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.stroke();
  ctx.setLineDash([R * 0.1, R * 0.12]); ctx.lineDashOffset = -ring; ctx.strokeStyle = ac; ctx.lineWidth = 2; glow(ac, 8);
  ctx.beginPath(); ctx.arc(ix, iy, ir * 0.62, 0, 7); ctx.stroke(); noGlow(); ctx.setLineDash([]);
  ctx.fillStyle = "#000"; ctx.beginPath();
  for (let k = 0; k < 6; k++) { const a = ring * 0.25 * rad + k * 60 * rad, px = ix + pr * Math.cos(a), py = iy + pr * Math.sin(a); if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
  ctx.closePath(); ctx.fill(); ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.globalAlpha = 0.5 + 0.3 * Math.sin(t * 4); ctx.fillStyle = rg(ix, iy, 0, pr * 0.6, [[0, ac], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(ix, iy, pr * 0.6, 0, 7); ctx.fill(); ctx.globalAlpha = 1;
  // reflexos úmidos que acompanham o olhar
  ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.beginPath(); ctx.ellipse(ix - ir * 0.38 - look.x * ir * 0.08, iy - ir * 0.42 - look.y * ir * 0.06, ir * 0.16, ir * 0.1, -0.6, 0, 7); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.35)"; ctx.beginPath(); ctx.arc(ix + ir * 0.4, iy + ir * 0.45, ir * 0.05, 0, 7); ctx.fill();
  ctx.fillStyle = lg(cx - LR, cy - LR, cx + LR * 0.3, cy + LR * 0.3, [[0, "rgba(255,255,255,0.28)"], [0.5, "rgba(255,255,255,0.03)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = R * 0.05; ctx.beginPath(); ctx.arc(cx, cy, LR * 0.86, 0.2 * Math.PI, 0.8 * Math.PI); ctx.stroke();
  // pálpebras mecânicas
  const cov = (1 - open) * LR, top = cy - LR + cov, bot = cy + LR - cov;
  ctx.fillStyle = lg(0, cy - LR * 1.1, 0, top + 0.01, [[0, "#232c42"], [1, "#8a9ab5"]]); ctx.fillRect(cx - LR * 1.1, cy - LR * 1.1, LR * 2.2, top - (cy - LR * 1.1));
  ctx.fillStyle = lg(0, bot, 0, cy + LR * 1.1, [[0, "#8a9ab5"], [1, "#232c42"]]); ctx.fillRect(cx - LR * 1.1, bot, LR * 2.2, cy + LR * 1.1 - bot);
  if (cov > 1) { ctx.strokeStyle = ac; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx - LR, top); ctx.lineTo(cx + LR, top); ctx.moveTo(cx - LR, bot); ctx.lineTo(cx + LR, bot); ctx.stroke(); }
  ctx.restore();
  ctx.strokeStyle = lg(cx - LR, cy - LR, cx + LR, cy + LR, [[0, "#e6eefc"], [0.5, "#4a5876"], [1, "#12192b"]]); ctx.lineWidth = R * 0.07;
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.stroke();
  glow(ac, 10 + hov * 10); ctx.strokeStyle = ac; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, cy, LR - R * 0.05, 0, 7); ctx.stroke(); noGlow();
  // HUD colorido
  for (let i = 0; i < 4; i++) { const a = i * 90 * rad; ctx.strokeStyle = PAL[i]; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * R * 1.78, cy + Math.sin(a) * R * 1.78); ctx.lineTo(cx + Math.cos(a) * R * 1.95, cy + Math.sin(a) * R * 1.95); ctx.stroke(); }
  ctx.lineWidth = 2; ctx.strokeStyle = PAL[1]; arcs(cx, cy, R * 1.88, ring * 0.6 * rad, 2, 35 * rad);
  ctx.strokeStyle = PAL[0]; arcs(cx, cy, R * 1.98, -ring * 0.4 * rad, 3, 28 * rad);
  ctx.strokeStyle = PAL[3]; arcs(cx, cy, R * 2.08, ring * 0.3 * rad, 4, 18 * rad);
  ctx.font = "10px 'Courier New', monospace"; ctx.fillStyle = ac;
  ctx.fillText("IRIS " + Math.round(pupil * 100) + "%", cx + R * 0.5, cy + R * 2.3);
  ctx.fillText(state.toUpperCase() + (ptr.active ? " • TRACK" : ""), cx - R * 1.4, cy + R * 2.3);
  drawPointerFx(t, ac);
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

// ---- ROBÔ DIGITAL ----
function drawRobot(t) {
  const cx = W / 2, cy = H * 0.34, S = Math.min(W * 0.5, H * 0.46), hw = S * 0.5, hh = S * 0.42, rad = Math.PI / 180;
  const ls = state === "listening", th = state === "thinking", sp = state === "speaking", ac = accent(), hue = HUES[palIdx];
  const dist = trackPointer(cx, cy - hh * 0.35, S * 1.5);
  const hovHead = ptr.active && Math.abs(ptr.x - cx) < hw * 1.2 && Math.abs(ptr.y - cy) < hh * 1.2 ? 1 : 0;
  startle = Math.max(0, startle - 0.025);
  const bob = Math.sin(t * 1.6) * S * 0.01;
  ctx.fillStyle = rg(cx, cy, 0, S * 1.2, [[0, "hsla(" + hue + ",100%,55%," + (0.16 + (ls ? 0.1 * Math.sin(t * 6) : 0) + hovHead * 0.08) + ")"], [0.6, "hsla(" + (hue + 80) + ",100%,60%,0.07)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, S * 1.2, 0, 7); ctx.fill();
  // nós HUD orbitando
  for (let i = 0; i < 5; i++) { const a = t * 0.6 + i * 1.2566, ox = cx + Math.cos(a) * S * 0.95, oy = cy + Math.sin(a) * S * 0.5; glow(PAL[i], 10); ctx.fillStyle = PAL[i]; ctx.beginPath(); ctx.arc(ox, oy, 3, 0, 7); ctx.fill(); noGlow(); }
  ctx.save(); ctx.translate(0, bob);

  // torso e ombros
  const ty = cy + hh * 1.3;
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.7)"; ctx.shadowBlur = 20;
  ctx.fillStyle = lg(0, ty, 0, H, [[0, "#b5c4dc"], [0.2, "#56647f"], [1, "#0b0f1a"]]);
  ctx.beginPath(); ctx.moveTo(cx - hw * 1.75, H); ctx.lineTo(cx - hw * 1.65, ty + hh * 0.35);
  ctx.quadraticCurveTo(cx - hw * 1.3, ty - hh * 0.05, cx - hw * 0.55, ty - hh * 0.05); ctx.lineTo(cx + hw * 0.55, ty - hh * 0.05);
  ctx.quadraticCurveTo(cx + hw * 1.3, ty - hh * 0.05, cx + hw * 1.65, ty + hh * 0.35); ctx.lineTo(cx + hw * 1.75, H); ctx.closePath(); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 3; ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx - hw * 1.6, ty + hh * 0.3); ctx.quadraticCurveTo(cx - hw * 1.25, ty, cx - hw * 0.55, ty); ctx.stroke();
  ctx.fillStyle = lg(0, ty, 0, ty + hh * 1.4, [[0, "#0d1220"], [1, "#05070d"]]); rr(cx - hw * 0.8, ty + hh * 0.05, hw * 1.6, hh * 1.4, 14); ctx.fill();
  ctx.strokeStyle = "hsla(" + hue + ",100%,60%,0.5)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const vy = ty + hh * (0.22 + i * 0.07); ctx.strokeStyle = "hsla(" + (hue + i * 30) + ",100%,60%,0.35)"; ctx.beginPath(); ctx.moveTo(cx - hw * 0.72, vy); ctx.lineTo(cx - hw * 0.48, vy); ctx.moveTo(cx + hw * 0.48, vy); ctx.lineTo(cx + hw * 0.72, vy); ctx.stroke(); }
  const rx = cx, ry = ty + hh * 0.62, r0 = hh * 0.26, cp = 0.8 + 0.2 * Math.sin(t * (sp ? 10 : 3)) + startle * 0.3;
  ctx.fillStyle = "#070a12"; ctx.beginPath(); ctx.arc(rx, ry, r0 * 1.4, 0, 7); ctx.fill();
  ctx.strokeStyle = lg(rx - r0, ry - r0, rx + r0, ry + r0, [[0, "#e4ecf9"], [1, "#2b354b"]]); ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(rx, ry, r0 * 1.4, 0, 7); ctx.stroke();
  glow(ac, 24); ctx.fillStyle = rg(rx, ry, 0, r0 * cp, [[0, "#ffffff"], [0.4, ac], [1, "hsl(" + hue + ",100%,22%)"]]); ctx.beginPath(); ctx.arc(rx, ry, r0 * cp, 0, 7); ctx.fill(); noGlow();
  ctx.strokeStyle = "rgba(255,255,255,0.8)"; ctx.lineWidth = 2; arcs(rx, ry, r0 * 1.15, ring * rad * 1.5, 3, 0.9);
  [-1, 1].forEach(function (s) {
    const jx = cx + s * hw * 1.5, jy = ty + hh * 0.5, jr = hh * 0.28;
    ctx.fillStyle = rg(jx - jr * 0.3, jy - jr * 0.3, 0, jr * 1.2, [[0, "#f1f6ff"], [0.5, "#7b8aa5"], [1, "#1c2438"]]); ctx.beginPath(); ctx.arc(jx, jy, jr, 0, 7); ctx.fill();
    ctx.strokeStyle = PAL[1]; ctx.lineWidth = 2; glow(PAL[1], 10); ctx.beginPath(); ctx.arc(jx, jy, jr * 0.7, 0, 7); ctx.stroke(); noGlow();
    ctx.fillStyle = ac; glow(ac, 12); ctx.beginPath(); ctx.arc(jx, jy, jr * 0.22, 0, 7); ctx.fill(); noGlow();
  });
  // sombra da cabeça no peito
  ctx.fillStyle = rg(cx, cy + hh * 1.15, 0, hw * 0.9, [[0, "rgba(0,0,0,0.5)"], [1, "rgba(0,0,0,0)"]]); ctx.fillRect(cx - hw, cy + hh * 0.9, hw * 2, hh * 0.6);

  // pescoço e cabos
  ctx.fillStyle = lg(cx - hw * 0.32, 0, cx + hw * 0.32, 0, [[0, "#10151f"], [0.5, "#8c9bb6"], [1, "#10151f"]]);
  ctx.fillRect(cx - hw * 0.32, cy + hh * 0.8, hw * 0.64, hh * 0.55);
  ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const y = cy + hh * (0.95 + i * 0.1); ctx.beginPath(); ctx.moveTo(cx - hw * 0.32, y); ctx.lineTo(cx + hw * 0.32, y); ctx.stroke(); }
  ctx.lineWidth = 3; ctx.strokeStyle = PAL[1]; ctx.beginPath(); ctx.moveTo(cx - hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx - hw * 0.55, cy + hh * 1.25, cx - hw * 0.6, cy + hh * 1.1, cx - hw * 0.7, ty + hh * 0.2); ctx.stroke();
  ctx.strokeStyle = PAL[0]; ctx.beginPath(); ctx.moveTo(cx + hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx + hw * 0.55, cy + hh * 1.25, cx + hw * 0.6, cy + hh * 1.1, cx + hw * 0.7, ty + hh * 0.2); ctx.stroke();

  // ---- cabeça (inclina na direção do ponteiro) ----
  ctx.save(); ctx.translate(cx, cy + hh); ctx.rotate(look.x * 0.06); ctx.translate(-cx, -(cy + hh));
  ctx.strokeStyle = "#8d9bb4"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(cx, cy - hh); ctx.lineTo(cx, cy - hh - S * 0.14); ctx.stroke();
  const ar = S * 0.035 * (1 + 0.4 * Math.sin(t * (th ? 12 : 4)) + startle);
  glow(ac, 20); ctx.fillStyle = rg(cx, cy - hh - S * 0.14, 0, ar, [[0, "#ffffff"], [1, ac]]); ctx.beginPath(); ctx.arc(cx, cy - hh - S * 0.14, ar, 0, 7); ctx.fill(); noGlow();
  if (th) { ctx.strokeStyle = ac; ctx.lineWidth = 2; arcs(cx, cy - hh - S * 0.14, S * 0.07, ring * rad * 2, 3, 1.0); }
  [-1, 1].forEach(function (s) {
    const ex = cx + s * (hw + S * 0.03), er = hh * 0.3;
    ctx.fillStyle = rg(ex - er * 0.3, cy - er * 0.3, 0, er * 1.3, [[0, "#eef4ff"], [0.5, "#6f7e9a"], [1, "#161d30"]]); ctx.beginPath(); ctx.arc(ex, cy, er, 0, 7); ctx.fill();
    ctx.strokeStyle = PAL[(s < 0 ? 1 : 2)]; ctx.lineWidth = 3; glow(PAL[(s < 0 ? 1 : 2)], 12); ctx.beginPath(); ctx.arc(ex, cy, er * 0.7, 0, 7); ctx.stroke(); noGlow();
    ctx.fillStyle = rg(ex, cy, 0, er * 0.45, [[0, "#ffffff"], [0.4, ac], [1, "#001018"]]); ctx.beginPath(); ctx.arc(ex, cy, er * 0.45, 0, 7); ctx.fill();
  });
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.75)"; ctx.shadowBlur = 26; ctx.shadowOffsetY = 10;
  ctx.fillStyle = lg(cx - hw, cy - hh, cx + hw, cy + hh, [[0, "#f4f8ff"], [0.18, "#b4c2da"], [0.5, "#4d5b78"], [0.85, "#1a2133"], [1, "#2c3652"]]);
  rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.fill(); ctx.restore();
  // textura de metal escovado (recortada no casco)
  ctx.save(); rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.clip();
  ctx.strokeStyle = "rgba(255,255,255,0.045)"; ctx.lineWidth = 1;
  for (let i = 0; i < 60; i++) { const y = cy - hh + (i / 60) * hh * 2; ctx.beginPath(); ctx.moveTo(cx - hw, y); ctx.lineTo(cx + hw, y + Math.sin(i) * 1.5); ctx.stroke(); }
  ctx.fillStyle = lg(cx - hw, cy - hh, cx - hw * 0.2, cy + hh * 0.2, [[0, "rgba(255,255,255,0.35)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.moveTo(cx - hw, cy - hh); ctx.lineTo(cx - hw * 0.1, cy - hh); ctx.lineTo(cx - hw, cy + hh * 0.5); ctx.closePath(); ctx.fill();
  ctx.fillStyle = lg(cx + hw, cy, cx + hw * 0.5, cy, [[0, "hsla(" + hue + ",100%,60%,0.35)"], [1, "rgba(0,0,0,0)"]]); ctx.fillRect(cx + hw * 0.5, cy - hh, hw * 0.5, hh * 2);
  ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2; rr(cx - hw + 3, cy - hh + 3, hw * 2 - 6, hh * 2 - 6, S * 0.115); ctx.stroke();
  ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 3; rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.stroke();
  ctx.strokeStyle = "rgba(0,0,0,0.4)"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx - hw * 0.9, cy - hh * 0.8); ctx.lineTo(cx + hw * 0.9, cy - hh * 0.8); ctx.moveTo(cx - hw * 0.93, cy - hh * 0.6); ctx.lineTo(cx - hw * 0.93, cy + hh * 0.6); ctx.moveTo(cx + hw * 0.93, cy - hh * 0.6); ctx.lineTo(cx + hw * 0.93, cy + hh * 0.6); ctx.stroke();
  [[-0.86, -0.86], [0.86, -0.86], [-0.86, 0.86], [0.86, 0.86]].forEach(function (p) { bolt(cx + p[0] * hw, cy + p[1] * hh, S * 0.017); });
  for (let i = 0; i < 5; i++) { const on = Math.sin(t * (2 + i * 1.3) * (1 + hovHead * 2)) > -0.2, c = PAL[i]; glow(c, on ? 10 : 0); ctx.fillStyle = on ? c : "#1b2236"; ctx.beginPath(); ctx.arc(cx - S * 0.1 + i * S * 0.05, cy - hh * 0.9, S * 0.012, 0, 7); ctx.fill(); noGlow(); }

  // visor de vidro
  const vx = cx - hw * 0.84, vy = cy - hh * 0.68, vw = hw * 1.68, vh = hh * 0.62;
  ctx.fillStyle = lg(0, vy, 0, vy + vh, [[0, "#04101e"], [0.5, "#020812"], [1, "#07182c"]]); rr(vx, vy, vw, vh, 16); ctx.fill();
  glow(ac, 8 + hovHead * 8); ctx.strokeStyle = ac; ctx.globalAlpha = 0.7; ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1; noGlow();
  ctx.save(); rr(vx, vy, vw, vh, 16); ctx.clip();
  ctx.strokeStyle = "hsla(" + hue + ",100%,60%,0.08)"; ctx.lineWidth = 1;
  for (let gx = vx; gx < vx + vw; gx += 14) { ctx.beginPath(); ctx.moveTo(gx, vy); ctx.lineTo(gx, vy + vh); ctx.stroke(); }
  for (let gy = vy; gy < vy + vh; gy += 14) { ctx.beginPath(); ctx.moveTo(vx, gy); ctx.lineTo(vx + vw, gy); ctx.stroke(); }
  if (th) { const sx = vx + (0.5 + 0.5 * Math.sin(t * 3)) * vw; ctx.fillStyle = lg(sx - 18, 0, sx + 18, 0, [[0, "rgba(255,176,46,0)"], [0.5, "rgba(255,176,46,0.4)"], [1, "rgba(255,176,46,0)"]]); ctx.fillRect(sx - 18, vy, 36, vh); }
  const er = hw * 0.2 * (1 + startle * 0.3), ey = cy - hh * 0.37 + look.y * hh * 0.06, lx = look.x * hw * 0.1;
  [-1, 1].forEach(function (s) {
    const ex = cx + s * hw * 0.42 + lx, k = (ls ? 1 + pulse * 0.2 : 1) * (1 + hovHead * 0.08);
    ctx.save(); ctx.translate(ex, ey); ctx.scale(1, Math.max(0.06, open));
    ctx.fillStyle = lg(-er, -er, er, er, [[0, "#cfd9ea"], [0.5, "#4b5a76"], [1, "#0e1424"]]); ctx.beginPath(); ctx.arc(0, 0, er * 1.28, 0, 7); ctx.fill();
    glow(ac, 24); ctx.fillStyle = rg(0, 0, 0, er * k, [[0, "#ffffff"], [0.2, ac], [0.5, "hsl(" + (hue + 40) + ",100%,45%)"], [0.8, "hsl(" + (hue + 100) + ",100%,25%)"], [1, "#00060e"]]); ctx.beginPath(); ctx.arc(0, 0, er * k, 0, 7); ctx.fill(); noGlow();
    ctx.strokeStyle = "hsla(" + hue + ",100%,75%,0.35)"; ctx.lineWidth = 1;
    for (let i = 0; i < 24; i++) { const a = i * 15 * rad; ctx.beginPath(); ctx.moveTo(Math.cos(a) * er * 0.35, Math.sin(a) * er * 0.35); ctx.lineTo(Math.cos(a) * er * 0.9 * k, Math.sin(a) * er * 0.9 * k); ctx.stroke(); }
    ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(look.x * er * 0.12, look.y * er * 0.12, er * 0.3 * pupil * (1 + startle * 0.4), 0, 7); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.92)"; ctx.beginPath(); ctx.arc(-er * 0.35 - look.x * er * 0.1, -er * 0.38 - look.y * er * 0.1, er * 0.14, 0, 7); ctx.fill();
    ctx.restore();
    ctx.save(); ctx.translate(ex, ey - er * 1.7 - startle * er * 0.5); ctx.rotate(s * (th ? -0.25 : ls ? 0.15 : 0.08 - hovHead * 0.12));
    glow(ac, 8); ctx.fillStyle = ac; rr(-er * 0.9, -3, er * 1.8, 6, 3); ctx.fill(); noGlow(); ctx.restore();
  });
  ctx.fillStyle = lg(vx, vy, vx + vw * 0.5, vy + vh, [[0, "rgba(255,255,255,0.18)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.moveTo(vx, vy); ctx.lineTo(vx + vw * 0.55, vy); ctx.lineTo(vx + vw * 0.25, vy + vh); ctx.lineTo(vx, vy + vh); ctx.closePath(); ctx.fill();
  ctx.restore();

  // traços de circuito coloridos
  ctx.lineWidth = 1.5;
  [-1, 1].forEach(function (s) {
    ctx.strokeStyle = s < 0 ? "rgba(255,43,214,0.7)" : "rgba(0,229,255,0.7)";
    ctx.beginPath(); ctx.moveTo(cx + s * hw * 0.92, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.66, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.3); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.42); ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(cx + s * hw * 0.55, cy + hh * 0.42, 3, 0, 7); ctx.fill();
  });

  // boca: grade com equalizador colorido (sorri quando o mouse está na cabeça)
  const gw = hw * 1.1, gh = hh * 0.34, gy = cy + hh * 0.42;
  ctx.fillStyle = "#04060c"; rr(cx - gw / 2, gy, gw, gh, 8); ctx.fill(); ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1.5; ctx.stroke();
  const bars = 11, bw = gw * 0.058, gap = gw * 0.03, total = bars * bw + (bars - 1) * gap, x0 = cx - total / 2, my = gy + gh / 2;
  for (let i = 0; i < bars; i++) {
    let h = gh * 0.05 + hovHead * gh * 0.08 * Math.abs(Math.sin(i * 0.6 - 2.2 + t * 2));
    if (hovHead && !sp && !ls) h += gh * 0.1 * (1 - Math.abs(i - 5) / 5);
    if (sp) h = gh * 0.1 + gh * 0.36 * Math.abs(Math.sin(t * 10 + i * 1.3) * Math.cos(t * 4 + i));
    else if (ls) h = gh * 0.05 + gh * 0.22 * pulse * Math.abs(Math.sin(t * 8 + i));
    const c = ls ? PAL[1] : "hsl(" + ((hue + i * 14) % 360) + ",100%,60%)";
    glow(c, 8); ctx.fillStyle = c; ctx.fillRect(x0 + i * (bw + gap), my - h, bw, h * 2); noGlow();
  }
  ctx.restore();   // fim da cabeça
  ctx.restore();   // fim do bob
  drawPointerFx(t, ac);
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

function drawEyeClassic(t) {
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

function drawRobotClassic(t) {
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
let col3, coreMesh, glowShell, arcLines, arcMat, frameN = 0; const sats = [];
function updateArcs() {
  const p = arcLines.geometry.attributes.position.array; let k = 0;
  for (let a = 0; a < 6; a++) {
    const th2 = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1);
    const dir = new THREE.Vector3(Math.sin(ph) * Math.cos(th2), Math.sin(ph) * Math.sin(th2), Math.cos(ph));
    let prev = dir.clone().multiplyScalar(0.9);
    for (let s = 1; s <= 10; s++) {
      const rr_ = 0.9 + (1.95 - 0.9) * s / 10;
      const nxt = dir.clone().multiplyScalar(rr_).add(new THREE.Vector3((Math.random() - 0.5) * 0.25, (Math.random() - 0.5) * 0.25, (Math.random() - 0.5) * 0.25));
      p[k++] = prev.x; p[k++] = prev.y; p[k++] = prev.z; p[k++] = nxt.x; p[k++] = nxt.y; p[k++] = nxt.z; prev = nxt;
    }
  }
  arcLines.geometry.attributes.position.needsUpdate = true;
}
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
  col3 = new THREE.Color(PAL[0]);
  sphereGroup.children[0].material = new THREE.MeshPhysicalMaterial({ color: 0x0e1424, metalness: 0.92, roughness: 0.18, clearcoat: 1, clearcoatRoughness: 0.08, transparent: true, opacity: 0.74 });
  coreMesh = new THREE.Mesh(new THREE.SphereGeometry(0.9, 32, 32), new THREE.MeshBasicMaterial({ color: 0x00e5ff }));
  sphereGroup.add(coreMesh);
  glowShell = new THREE.Mesh(new THREE.SphereGeometry(2.3, 48, 48), new THREE.ShaderMaterial({
    uniforms: { c: { value: new THREE.Color(0x00e5ff) } },
    vertexShader: "varying vec3 vN; varying vec3 vV; void main() { vN = normalize(normalMatrix * normal); vec4 mv = modelViewMatrix * vec4(position, 1.0); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }",
    fragmentShader: "uniform vec3 c; varying vec3 vN; varying vec3 vV; void main() { float f = pow(1.0 - abs(dot(vN, vV)), 2.6); gl_FragColor = vec4(c * f * 1.8, f); }",
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  sphereGroup.add(glowShell);
  arcMat = new THREE.LineBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending });
  const arcGeo = new THREE.BufferGeometry(); arcGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(360), 3));
  arcLines = new THREE.LineSegments(arcGeo, arcMat); sphereGroup.add(arcLines);
  for (let i = 0; i < 5; i++) { const sm = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 16), new THREE.MeshBasicMaterial({ color: PAL[i] })); scene.add(sm); sats.push({ m: sm, r: 3.1 + i * 0.35, sp: 0.5 + i * 0.17, inc: i * 0.6, ph: i * 1.3 }); }
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
    startle = Math.max(0, startle - 0.025);
    if (ptr.active && performance.now() - ptr.last > 4000) ptr.active = false;
    col3.set(PAL[palIdx]);
    const bw3 = box.clientWidth || 300, bh3 = box.clientHeight || 300;
    const hov3 = ptr.active && Math.hypot(ptr.x - bw3 / 2, ptr.y - bh3 / 2) < Math.min(bw3, bh3) * 0.3;
    coreMesh.material.color.copy(col3);
    const cs = 0.9 + 0.1 * Math.sin(t * 3) + (sp ? 0.12 * Math.abs(Math.sin(t * 12)) : 0) + startle * 0.5 + (hov3 ? 0.1 : 0);
    coreMesh.scale.set(cs, cs, cs);
    glowShell.material.uniforms.c.value.copy(col3); glowShell.scale.setScalar(1 + (hov3 ? 0.04 : 0) + startle * 0.08);
    wireMaterial.color.copy(col3); arcMat.color.copy(col3); pointLight1.color.copy(col3);
    if ((frameN++ % 3) === 0) updateArcs();
    if (hov3) sphereGroup.rotation.y += 0.02;
    sats.forEach(function (s) { const a = t * s.sp + s.ph; s.m.position.set(Math.cos(a) * s.r, Math.sin(a) * Math.sin(s.inc) * s.r, Math.sin(a) * Math.cos(s.inc) * s.r); });
    if (ptr.active) { camera.position.x += ((ptr.x / bw3 - 0.5) * 2.4 - camera.position.x) * 0.06; camera.position.y += ((0.5 - ptr.y / bh3) * 1.6 - camera.position.y) * 0.06; }
    else { camera.position.x *= 0.95; camera.position.y *= 0.95; }
    camera.lookAt(0, 0, 0);
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
    st.subheader("🎙️ Voz")
    stt_label = st.selectbox("👂 Reconhecimento", ["Whisper (Groq) — melhor no celular", "Navegador — mais rápido, menos preciso"], key="stt_sel")
    tts_label = st.selectbox("🔊 Motor de voz", ["Neural (Edge) — voz natural", "Navegador — voz do aparelho"], key="tts_sel")
    _vozes = listar_vozes_edge()
    voz_nome = st.selectbox("🗣️ Voz do Zeus", list(_vozes.keys()), key="voz_sel")
    tts_rate = st.slider("⚡ Velocidade da fala", 0.8, 1.5, 1.0, 0.05, key="rate_sel")
    silence_s = st.slider("⏱️ Silêncio para enviar (s)", 1, 10, 2, key="silence_sel")
    stt_key = "whisper" if stt_label.startswith("Whisper") else "browser"
    tts_key = "edge" if tts_label.startswith("Neural") else "browser"
    voz_edge = _vozes[voz_nome]
    grupo_voz = st.selectbox("🌍 Mais vozes", ["Só português (padrão)", "Multilíngues (falam português)", "Todas as vozes (outros idiomas)"], key="grupo_voz")
    if grupo_voz != "Só português (padrão)":
        _todas = listar_todas_vozes_edge()
        _filtro = {k_: v_ for k_, v_ in _todas.items() if "Multilingual" in v_} if grupo_voz.startswith("Multi") else _todas
        if _filtro:
            _nome_extra = st.selectbox("🗣️ Escolha a voz", list(_filtro.keys()), key="voz_extra_sel")
            voz_edge = _filtro[_nome_extra]
    _tom = st.slider("🎚️ Tom da voz (Hz)", -50, 50, 0, 5, key="tom_sel")
    _vol = st.slider("🔉 Volume da voz (%)", -50, 50, 0, 5, key="vol_sel")
    PITCH_ATUAL = f"{_tom:+d}Hz"
    VOLUME_ATUAL = f"{_vol:+d}%"
    if st.button("🔊 Testar voz"):
        _mp3 = sintetizar("Olá, eu sou o Zeus. Como posso ajudar?", voz_edge, tts_rate)
        if _mp3:
            st.audio(_mp3, format="audio/mp3")
        else:
            st.warning("Não consegui gerar a voz neural: " + st.session_state.get("tts_erro", "erro desconhecido"))
    if st.button("🗑️ Limpar conversa"):
        st.session_state.messages = []
        st.session_state.avatar_reply = None
        st.rerun()

    st.divider()
    st.subheader("🌐 Internet")
    st.selectbox("Busca na web", ["Automática", "Sempre", "Desligada"], key="web_mode")
    st.divider()
    render_camera_sidebar()

    st.divider()
    render_visao_gerar_sidebar()

    st.divider()
    if st.button("🧪 Testar chaves", key="btn_test_keys"):
        st.session_state.teste_chaves = testar_chaves()
    for _l in st.session_state.get("teste_chaves", []):
        st.write(_l)
    st.subheader("🔑 Status das Chaves")
    for k in KEYS.values():
        st.write(("✅ " if _secret(k) else "❌ ") + k)
    if st.session_state.get("last_info"):
        st.caption(st.session_state.last_info)

inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE")
render_avatar_voice(AVATAR_MAP[avatar_type], silence_s, stt_key, tts_key, voz_edge, tts_rate)
render_pipeline_ui()
render_estudio_ui()
render_evolucao_ui()

with st.expander("📜 Histórico da conversa"):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
