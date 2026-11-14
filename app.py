# ============================================================
# ZEUS 3.0 - app.py
# CHAVES: nunca coloque no código. Use .streamlit/secrets.toml (e adicione ao .gitignore)
# ou Settings > Secrets no Streamlit Cloud:
#   GROQ_API_KEY = "..."          GEMINI_API_KEY = "..."
#   ANTHROPIC_API_KEY = "..."     HUGGINGFACE_API_KEY = "..."
#   GITHUB_API_KEY = "..."        (token com acesso ao GitHub Models)
#   DEEPINFRA_API_KEY = "..."     (deepinfra.com/dash/api_keys: Dolphin, imagem e edição de foto)
# Opcionais (busca Google oficial): GOOGLE_API_KEY e GOOGLE_CSE_ID
# Dependências: streamlit requests edge-tts duckduckgo-search
# ============================================================
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

PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY", "deepinfra": "DEEPINFRA_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL", "huggingface": "HF_MODEL", "github": "GITHUB_MODEL", "deepinfra": "DEEPINFRA_MODEL"}
MODELS = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "gemini": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-2.5-flash"],
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["cognitivecomputations/dolphin-2.6-mixtral-8x7b", "meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3"],
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


def _call_gemini(api_key, model, msgs, system=None):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
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
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)


CALLERS = {"groq": _call_groq, "gemini": _call_gemini, "claude": _call_claude,
           "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra}


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


async def _edge_synth(texto, voz, rate):
    import edge_tts
    chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
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
    body = {"prompt": prompt, "size": "1024x1024", "n": 1}
    if _secret("DEEPINFRA_IMAGE_MODEL"):
        body["model"] = _secret("DEEPINFRA_IMAGE_MODEL")  # padrão da DeepInfra: FLUX Schnell
    r = requests.post("https://api.deepinfra.com/v1/openai/images/generations",
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])


IMAGE_CASCADE = [("DeepInfra", _img_deepinfra), ("Hugging Face", _img_hf), ("Gemini", _img_gemini),
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


EDIT_CASCADE = [("DeepInfra (edita a sua foto)", _edit_deepinfra), ("Gemini (edita a sua foto)", _edit_gemini),
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


def _vis_gemini(k, b, m, p):
    model = _secret("GEMINI_VISION_MODEL") or "gemini-flash-latest"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"Content-Type": "application/json", "x-goog-api-key": k},
                      json={"contents": [{"role": "user", "parts": [{"text": p}, {"inline_data": {"mime_type": m, "data": b}}]}]},
                      timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return "".join(x.get("text", "") for x in r.json()["candidates"][0]["content"]["parts"]).strip()


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


VISION_CASCADE = {"deepinfra": _vis_deepinfra, "groq": _vis_groq, "gemini": _vis_gemini, "claude": _vis_claude, "huggingface": _vis_hf, "github": _vis_github}


def ask_vision(img_bytes, comando):
    """Retorna (texto, provedor, log). Começa pelo modelo escolhido e cai em cascata."""
    img_b64, mime = base64.b64encode(img_bytes).decode(), _mime_img(img_bytes)
    escolhido = st.session_state.get("model_sel", "groq")
    ordem = [escolhido] + [p for p in PROVIDERS if p != escolhido]
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


def executar_estudio(img, comando, acao):
    log = []
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
    bruto = _llm("Photo description:\n" + (desc or "(unavailable)") + "\n\nUser request (Portuguese): " + (comando or "melhore esta foto"),
                 sys_p, log, "Comando para as IAs de imagem")
    m_e = re.search(r"EDIT:\s*(.+)", bruto or "")
    m_v = re.search(r"VIDEO:\s*(.+)", bruto or "")
    res["prompt_edit"] = m_e.group(1).strip() if m_e else (comando or "enhance this photo, keep identity")
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
    if res.get("vid"):
        st.video(res["vid"])
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
        if st.button("🚀 Executar", key="estudio_run", disabled=not foto):
            with st.spinner("As IAs estão conversando entre si..."):
                st.session_state.estudio_res = executar_estudio(foto, comando, acao)
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
function accent() { return state === "listening" ? "#ff2bd6" : state === "thinking" ? "#ffb02e" : state === "speaking" ? "#3dffb0" : "#00e5ff"; }

// ---- OLHO CIBERNÉTICO (carcaça metálica, íris com fibras, diafragma, pálpebras mecânicas) ----
function drawEye(t) {
  const cx = W / 2, cy = H * 0.36, R = Math.min(W * 0.26, H * 0.17), rad = Math.PI / 180, ac = accent();
  const ls = state === "listening", th = state === "thinking";
  ctx.fillStyle = rg(cx, cy, R * 0.9, R * 2.4, [[0, "rgba(0,229,255,0.28)"], [0.5, "rgba(140,50,255,0.12)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 2.4, 0, 7); ctx.fill();
  // carcaça metálica
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.85)"; ctx.shadowBlur = 26; ctx.shadowOffsetY = 10;
  ctx.fillStyle = lg(cx - R * 1.7, cy - R * 1.7, cx + R * 1.7, cy + R * 1.7, [[0, "#f2f7ff"], [0.22, "#93a4bf"], [0.5, "#2b354b"], [0.78, "#0e1321"], [1, "#6a7894"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.72, 0, 7); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.45)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, R * 1.69, Math.PI * 0.95, Math.PI * 1.7); ctx.stroke();
  for (let i = 0; i < 8; i++) { const a = (i * 45 + 22.5) * rad; bolt(cx + Math.cos(a) * R * 1.6, cy + Math.sin(a) * R * 1.6, R * 0.05); }
  // moldura escura + anel de LEDs segmentado
  ctx.fillStyle = lg(cx, cy - R * 1.5, cx, cy + R * 1.5, [[0, "#03050a"], [1, "#182034"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.5, 0, 7); ctx.fill();
  const span = th ? 130 : ls ? 220 : 90;
  for (let i = 0; i < 48; i++) {
    const a0 = i * 7.5, lit = (((a0 - ring) % 360) + 360) % 360 < span;
    ctx.strokeStyle = lit ? ac : "rgba(255,255,255,0.07)"; ctx.lineWidth = R * 0.07;
    if (lit) glow(ac, 9);
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.38, a0 * rad, (a0 + 5) * rad); ctx.stroke(); noGlow();
  }
  // lente + íris
  const LR = R * 1.2;
  ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.clip();
  ctx.fillStyle = rg(cx, cy, 0, LR, [[0, "#0a1a2e"], [1, "#010308"]]); ctx.fillRect(cx - LR, cy - LR, LR * 2, LR * 2);
  const ix = cx + look.x * R * 0.32, iy = cy + look.y * R * 0.22, ir = R * 0.88, pr = ir * 0.34 * pupil;
  ctx.fillStyle = rg(ix, iy, 0, ir, [[0, "#02121f"], [0.3, "#0aa8d8"], [0.62, "#3a52ff"], [0.88, "#9a2cff"], [1, "#07101f"]]);
  ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.fill();
  for (let i = 0; i < 150; i++) {
    const a = i * 2.399963, r0 = pr * 1.05, r1 = ir * (0.72 + ((i * 53) % 28) / 100);
    ctx.strokeStyle = "hsla(" + (175 + (i * 37) % 110) + ",100%," + (45 + (i * 13) % 25) + "%,0.38)"; ctx.lineWidth = 1.1;
    ctx.beginPath(); ctx.moveTo(ix + r0 * Math.cos(a), iy + r0 * Math.sin(a)); ctx.lineTo(ix + r1 * Math.cos(a), iy + r1 * Math.sin(a)); ctx.stroke();
  }
  ctx.strokeStyle = "rgba(0,0,12,0.85)"; ctx.lineWidth = R * 0.09; ctx.beginPath(); ctx.arc(ix, iy, ir, 0, 7); ctx.stroke();
  ctx.setLineDash([R * 0.1, R * 0.12]); ctx.lineDashOffset = -ring; ctx.strokeStyle = ac; ctx.lineWidth = 2; glow(ac, 8);
  ctx.beginPath(); ctx.arc(ix, iy, ir * 0.58, 0, 7); ctx.stroke(); noGlow(); ctx.setLineDash([]);
  // pupila com diafragma hexagonal
  ctx.fillStyle = "#000"; ctx.beginPath();
  for (let k = 0; k < 6; k++) { const a = ring * 0.25 * rad + k * 60 * rad, px = ix + pr * Math.cos(a), py = iy + pr * Math.sin(a); if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
  ctx.closePath(); ctx.fill(); ctx.strokeStyle = "rgba(0,229,255,0.7)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.globalAlpha = 0.5 + 0.3 * Math.sin(t * 4); ctx.fillStyle = rg(ix, iy, 0, pr * 0.6, [[0, ac], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(ix, iy, pr * 0.6, 0, 7); ctx.fill(); ctx.globalAlpha = 1;
  // reflexos de vidro
  ctx.fillStyle = "rgba(255,255,255,0.75)"; ctx.beginPath(); ctx.ellipse(ix - ir * 0.38, iy - ir * 0.42, ir * 0.16, ir * 0.1, -0.6, 0, 7); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.35)"; ctx.beginPath(); ctx.arc(ix + ir * 0.4, iy + ir * 0.45, ir * 0.05, 0, 7); ctx.fill();
  ctx.fillStyle = lg(cx - LR, cy - LR, cx + LR * 0.3, cy + LR * 0.3, [[0, "rgba(255,255,255,0.26)"], [0.5, "rgba(255,255,255,0.03)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.fill();
  // pálpebras mecânicas (piscar)
  const cov = (1 - open) * LR, top = cy - LR + cov, bot = cy + LR - cov;
  ctx.fillStyle = lg(0, cy - LR * 1.1, 0, top + 0.01, [[0, "#232c42"], [1, "#8a9ab5"]]); ctx.fillRect(cx - LR * 1.1, cy - LR * 1.1, LR * 2.2, top - (cy - LR * 1.1));
  ctx.fillStyle = lg(0, bot, 0, cy + LR * 1.1, [[0, "#8a9ab5"], [1, "#232c42"]]); ctx.fillRect(cx - LR * 1.1, bot, LR * 2.2, cy + LR * 1.1 - bot);
  if (cov > 1) { ctx.strokeStyle = ac; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx - LR, top); ctx.lineTo(cx + LR, top); ctx.moveTo(cx - LR, bot); ctx.lineTo(cx + LR, bot); ctx.stroke(); }
  ctx.restore();
  // aro da lente
  ctx.strokeStyle = lg(cx - LR, cy - LR, cx + LR, cy + LR, [[0, "#e6eefc"], [0.5, "#4a5876"], [1, "#12192b"]]); ctx.lineWidth = R * 0.07;
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.stroke();
  glow(ac, 10); ctx.strokeStyle = ac; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, cy, LR - R * 0.05, 0, 7); ctx.stroke(); noGlow();
  // HUD
  ctx.strokeStyle = "rgba(0,229,255,0.6)"; ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const a = i * 90 * rad; ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * R * 1.78, cy + Math.sin(a) * R * 1.78); ctx.lineTo(cx + Math.cos(a) * R * 1.95, cy + Math.sin(a) * R * 1.95); ctx.stroke(); }
  ctx.lineWidth = 2; ctx.strokeStyle = MG; arcs(cx, cy, R * 1.88, ring * 0.6 * rad, 2, 35 * rad);
  ctx.strokeStyle = "#1e90ff"; arcs(cx, cy, R * 1.98, -ring * 0.4 * rad, 3, 28 * rad);
  ctx.font = "10px 'Courier New', monospace"; ctx.fillStyle = ac;
  ctx.fillText("IRIS " + Math.round(pupil * 100) + "%", cx + R * 0.5, cy + R * 2.15);
  ctx.fillText(state.toUpperCase(), cx - R * 1.4, cy + R * 2.15);
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

// ---- ROBÔ DIGITAL (casco metálico chanfrado, visor de vidro, olhos-lente, fones, reator no peito) ----
function drawRobot(t) {
  const cx = W / 2, cy = H * 0.34, S = Math.min(W * 0.5, H * 0.46), hw = S * 0.5, hh = S * 0.42, rad = Math.PI / 180;
  const ls = state === "listening", th = state === "thinking", sp = state === "speaking", ac = accent();
  ctx.fillStyle = rg(cx, cy, 0, S * 1.15, [[0, "rgba(0,229,255," + (0.16 + (ls ? 0.1 * Math.sin(t * 6) : 0)) + ")"], [0.6, "rgba(140,50,255,0.07)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, S * 1.15, 0, 7); ctx.fill();

  // torso e ombros
  const ty = cy + hh * 1.3;
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.7)"; ctx.shadowBlur = 20;
  ctx.fillStyle = lg(0, ty, 0, H, [[0, "#a7b7d1"], [0.2, "#56647f"], [1, "#0b0f1a"]]);
  ctx.beginPath(); ctx.moveTo(cx - hw * 1.75, H); ctx.lineTo(cx - hw * 1.65, ty + hh * 0.35);
  ctx.quadraticCurveTo(cx - hw * 1.3, ty - hh * 0.05, cx - hw * 0.55, ty - hh * 0.05); ctx.lineTo(cx + hw * 0.55, ty - hh * 0.05);
  ctx.quadraticCurveTo(cx + hw * 1.3, ty - hh * 0.05, cx + hw * 1.65, ty + hh * 0.35); ctx.lineTo(cx + hw * 1.75, H); ctx.closePath(); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 3; ctx.stroke();
  ctx.fillStyle = lg(0, ty, 0, ty + hh * 1.4, [[0, "#0d1220"], [1, "#05070d"]]); rr(cx - hw * 0.8, ty + hh * 0.05, hw * 1.6, hh * 1.4, 14); ctx.fill();
  ctx.strokeStyle = "rgba(0,229,255,0.35)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.12)"; ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const vy = ty + hh * (0.22 + i * 0.07); ctx.beginPath(); ctx.moveTo(cx - hw * 0.72, vy); ctx.lineTo(cx - hw * 0.48, vy); ctx.moveTo(cx + hw * 0.48, vy); ctx.lineTo(cx + hw * 0.72, vy); ctx.stroke(); }
  // reator do peito
  const rx = cx, ry = ty + hh * 0.62, r0 = hh * 0.26, cp = 0.8 + 0.2 * Math.sin(t * (sp ? 10 : 3)) + pulse * 0.2;
  ctx.fillStyle = "#070a12"; ctx.beginPath(); ctx.arc(rx, ry, r0 * 1.4, 0, 7); ctx.fill();
  ctx.strokeStyle = lg(rx - r0, ry - r0, rx + r0, ry + r0, [[0, "#e4ecf9"], [1, "#2b354b"]]); ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(rx, ry, r0 * 1.4, 0, 7); ctx.stroke();
  glow("#ff9a1f", 22); ctx.fillStyle = rg(rx, ry, 0, r0 * cp, [[0, "#fffbe0"], [0.4, "#ffb02e"], [1, "#b83a00"]]); ctx.beginPath(); ctx.arc(rx, ry, r0 * cp, 0, 7); ctx.fill(); noGlow();
  ctx.strokeStyle = "rgba(255,200,80,0.85)"; ctx.lineWidth = 2; arcs(rx, ry, r0 * 1.15, ring * rad * 1.5, 3, 0.9);
  // juntas dos ombros
  [-1, 1].forEach(function (s) {
    const jx = cx + s * hw * 1.5, jy = ty + hh * 0.5, jr = hh * 0.28;
    ctx.fillStyle = rg(jx - jr * 0.3, jy - jr * 0.3, 0, jr * 1.2, [[0, "#f1f6ff"], [0.5, "#7b8aa5"], [1, "#1c2438"]]); ctx.beginPath(); ctx.arc(jx, jy, jr, 0, 7); ctx.fill();
    ctx.strokeStyle = MG; ctx.lineWidth = 2; glow(MG, 10); ctx.beginPath(); ctx.arc(jx, jy, jr * 0.7, 0, 7); ctx.stroke(); noGlow();
    ctx.fillStyle = CY; glow(CY, 12); ctx.beginPath(); ctx.arc(jx, jy, jr * 0.22, 0, 7); ctx.fill(); noGlow();
  });

  // pescoço com anéis e cabos
  ctx.fillStyle = lg(cx - hw * 0.32, 0, cx + hw * 0.32, 0, [[0, "#10151f"], [0.5, "#7d8ca7"], [1, "#10151f"]]);
  ctx.fillRect(cx - hw * 0.32, cy + hh * 0.8, hw * 0.64, hh * 0.55);
  ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const y = cy + hh * (0.95 + i * 0.1); ctx.beginPath(); ctx.moveTo(cx - hw * 0.32, y); ctx.lineTo(cx + hw * 0.32, y); ctx.stroke(); }
  ctx.lineWidth = 3; ctx.strokeStyle = MG; ctx.beginPath(); ctx.moveTo(cx - hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx - hw * 0.55, cy + hh * 1.25, cx - hw * 0.6, cy + hh * 1.1, cx - hw * 0.7, ty + hh * 0.2); ctx.stroke();
  ctx.strokeStyle = CY; ctx.beginPath(); ctx.moveTo(cx + hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx + hw * 0.55, cy + hh * 1.25, cx + hw * 0.6, cy + hh * 1.1, cx + hw * 0.7, ty + hh * 0.2); ctx.stroke();

  // antena
  ctx.strokeStyle = "#8d9bb4"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(cx, cy - hh); ctx.lineTo(cx, cy - hh - S * 0.14); ctx.stroke();
  const ar = S * 0.035 * (1 + 0.4 * Math.sin(t * (th ? 12 : 4)));
  glow(MG, 18); ctx.fillStyle = rg(cx, cy - hh - S * 0.14, 0, ar, [[0, "#ffd0f5"], [1, MG]]); ctx.beginPath(); ctx.arc(cx, cy - hh - S * 0.14, ar, 0, 7); ctx.fill(); noGlow();
  if (th) { ctx.strokeStyle = MG; ctx.lineWidth = 2; arcs(cx, cy - hh - S * 0.14, S * 0.07, ring * rad * 2, 3, 1.0); }

  // fones laterais (orelhas)
  [-1, 1].forEach(function (s) {
    const ex = cx + s * (hw + S * 0.03), er = hh * 0.3;
    ctx.fillStyle = rg(ex - er * 0.3, cy - er * 0.3, 0, er * 1.3, [[0, "#eef4ff"], [0.5, "#6f7e9a"], [1, "#161d30"]]); ctx.beginPath(); ctx.arc(ex, cy, er, 0, 7); ctx.fill();
    ctx.strokeStyle = MG; ctx.lineWidth = 3; glow(MG, 12); ctx.beginPath(); ctx.arc(ex, cy, er * 0.7, 0, 7); ctx.stroke(); noGlow();
    ctx.fillStyle = rg(ex, cy, 0, er * 0.45, [[0, "#ffffff"], [0.4, ac], [1, "#001018"]]); ctx.beginPath(); ctx.arc(ex, cy, er * 0.45, 0, 7); ctx.fill();
  });

  // casco da cabeça (metal chanfrado)
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.75)"; ctx.shadowBlur = 26; ctx.shadowOffsetY = 10;
  ctx.fillStyle = lg(cx - hw, cy - hh, cx + hw, cy + hh, [[0, "#eef4ff"], [0.18, "#a9b8d1"], [0.5, "#4d5b78"], [0.85, "#1a2133"], [1, "#2c3652"]]);
  rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2; rr(cx - hw + 3, cy - hh + 3, hw * 2 - 6, hh * 2 - 6, S * 0.115); ctx.stroke();
  ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 3; rr(cx - hw, cy - hh, hw * 2, hh * 2, S * 0.12); ctx.stroke();
  ctx.strokeStyle = "rgba(0,0,0,0.4)"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx - hw * 0.9, cy - hh * 0.8); ctx.lineTo(cx + hw * 0.9, cy - hh * 0.8); ctx.moveTo(cx - hw * 0.93, cy - hh * 0.6); ctx.lineTo(cx - hw * 0.93, cy + hh * 0.6); ctx.moveTo(cx + hw * 0.93, cy - hh * 0.6); ctx.lineTo(cx + hw * 0.93, cy + hh * 0.6); ctx.stroke();
  [[-0.86, -0.86], [0.86, -0.86], [-0.86, 0.86], [0.86, 0.86]].forEach(function (p) { bolt(cx + p[0] * hw, cy + p[1] * hh, S * 0.017); });
  const leds = ["#3dffb0", "#ffb02e", "#ff2bd6"];
  for (let i = 0; i < 3; i++) { const on = Math.sin(t * (2 + i * 1.3)) > -0.2; glow(leds[i], on ? 10 : 0); ctx.fillStyle = on ? leds[i] : "#1b2236"; ctx.beginPath(); ctx.arc(cx - S * 0.05 + i * S * 0.05, cy - hh * 0.9, S * 0.012, 0, 7); ctx.fill(); noGlow(); }

  // visor de vidro
  const vx = cx - hw * 0.84, vy = cy - hh * 0.68, vw = hw * 1.68, vh = hh * 0.62;
  ctx.fillStyle = lg(0, vy, 0, vy + vh, [[0, "#04101e"], [0.5, "#020812"], [1, "#07182c"]]); rr(vx, vy, vw, vh, 16); ctx.fill();
  glow(ac, 8); ctx.strokeStyle = ac; ctx.globalAlpha = 0.65; ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1; noGlow();
  ctx.save(); rr(vx, vy, vw, vh, 16); ctx.clip();
  ctx.strokeStyle = "rgba(0,229,255,0.07)"; ctx.lineWidth = 1;
  for (let gx = vx; gx < vx + vw; gx += 14) { ctx.beginPath(); ctx.moveTo(gx, vy); ctx.lineTo(gx, vy + vh); ctx.stroke(); }
  for (let gy = vy; gy < vy + vh; gy += 14) { ctx.beginPath(); ctx.moveTo(vx, gy); ctx.lineTo(vx + vw, gy); ctx.stroke(); }
  if (th) { const sx = vx + (0.5 + 0.5 * Math.sin(t * 3)) * vw; ctx.fillStyle = lg(sx - 18, 0, sx + 18, 0, [[0, "rgba(255,176,46,0)"], [0.5, "rgba(255,176,46,0.35)"], [1, "rgba(255,176,46,0)"]]); ctx.fillRect(sx - 18, vy, 36, vh); }
  const er = hw * 0.2, ey = cy - hh * 0.37 + look.y * hh * 0.05, lx = look.x * hw * 0.07;
  [-1, 1].forEach(function (s) {
    const ex = cx + s * hw * 0.42 + lx, k = ls ? 1 + pulse * 0.2 : 1;
    ctx.save(); ctx.translate(ex, ey); ctx.scale(1, Math.max(0.06, open));
    ctx.fillStyle = lg(-er, -er, er, er, [[0, "#cfd9ea"], [0.5, "#4b5a76"], [1, "#0e1424"]]); ctx.beginPath(); ctx.arc(0, 0, er * 1.28, 0, 7); ctx.fill();
    glow(ac, 22); ctx.fillStyle = rg(0, 0, 0, er * k, [[0, "#ffffff"], [0.22, ac], [0.7, "#0a3a66"], [1, "#00060e"]]); ctx.beginPath(); ctx.arc(0, 0, er * k, 0, 7); ctx.fill(); noGlow();
    ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(0, 0, er * 0.3 * pupil, 0, 7); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(0, 0, er * 0.62, 0, 7); ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.9)"; ctx.beginPath(); ctx.arc(-er * 0.35, -er * 0.38, er * 0.14, 0, 7); ctx.fill();
    ctx.restore();
    ctx.save(); ctx.translate(ex, ey - er * 1.7); ctx.rotate(s * (th ? -0.25 : ls ? 0.15 : 0.08));
    glow(ac, 8); ctx.fillStyle = ac; rr(-er * 0.9, -3, er * 1.8, 6, 3); ctx.fill(); noGlow(); ctx.restore();
  });
  ctx.fillStyle = lg(vx, vy, vx + vw * 0.5, vy + vh, [[0, "rgba(255,255,255,0.16)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.moveTo(vx, vy); ctx.lineTo(vx + vw * 0.55, vy); ctx.lineTo(vx + vw * 0.25, vy + vh); ctx.lineTo(vx, vy + vh); ctx.closePath(); ctx.fill();
  ctx.restore();

  // traços de circuito coloridos nas bochechas
  ctx.lineWidth = 1.5;
  [-1, 1].forEach(function (s) {
    ctx.strokeStyle = s < 0 ? "rgba(255,43,214,0.6)" : "rgba(0,229,255,0.6)";
    ctx.beginPath(); ctx.moveTo(cx + s * hw * 0.92, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.66, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.3); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.42); ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(cx + s * hw * 0.55, cy + hh * 0.42, 3, 0, 7); ctx.fill();
  });

  // boca: grade com equalizador colorido
  const gw = hw * 1.1, gh = hh * 0.34, gy = cy + hh * 0.42;
  ctx.fillStyle = "#04060c"; rr(cx - gw / 2, gy, gw, gh, 8); ctx.fill(); ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1.5; ctx.stroke();
  const bars = 11, bw = gw * 0.058, gap = gw * 0.03, total = bars * bw + (bars - 1) * gap, x0 = cx - total / 2, my = gy + gh / 2;
  for (let i = 0; i < bars; i++) {
    let h = gh * 0.05;
    if (sp) h = gh * 0.1 + gh * 0.36 * Math.abs(Math.sin(t * 10 + i * 1.3) * Math.cos(t * 4 + i));
    else if (ls) h = gh * 0.05 + gh * 0.22 * pulse * Math.abs(Math.sin(t * 8 + i));
    const c = ls ? MG : "hsl(" + (150 + i * 14) + ",100%,60%)";
    glow(c, 8); ctx.fillStyle = c; ctx.fillRect(x0 + i * (bw + gap), my - h, bw, h * 2); noGlow();
  }
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

with st.expander("📜 Histórico da conversa"):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
