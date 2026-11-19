# ============================================================
# ZEUS 3.0 - app.py COMPLETO - REESCRITO
# NÃO MUDA NOME DE CHAVE - usa Secrets do Streamlit
# B2 direto - sem 【entity-GitHub¦canonical_name=GitHub】 para aprendizado - Telegram 24h
# ============================================================
import ast, asyncio, base64, hashlib, importlib, inspect, io, json, os, re, sqlite3, sys, tempfile, time, zipfile, contextlib, logging, threading, requests
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")
_log = logging.getLogger("zeus.cofre")

THEMES = {"Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"}}
THEME_CSS = """<style>.stApp { background-color: __BG__; color: __TEXT__; font-family: 'Courier New', monospace; }[data-testid="stSidebar"] { background-color: __PANEL__; }h1, h2, h3 { color: __PRIMARY__ !important; text-shadow: 0 0 10px __BORDER__; }</style>"""
def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    css = THEME_CSS
    for token, chave in (("__BG__", "bg"), ("__TEXT__", "text"), ("__PANEL__", "panel"), ("__PRIMARY__", "primary"), ("__BORDER__", "border")):
        css = css.replace(token, t[chave])
    st.markdown(css, unsafe_allow_html=True)

# ============================================================
# CHAVES - SEMPRE VIA _secret() - NUNCA NO CÓDIGO
# ============================================================
CHAVES_PADRAO = {}
def _limpar_chave(v: str) -> str:
    if v is None: return ""
    v = str(v).strip().strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v

def _secret(key: str, default: str = "") -> str:
    try:
        sec = st.secrets
        val = None
        try: val = sec.get(key)
        except Exception: val = None
        if not val:
            try:
                for _, bloco in sec.items():
                    if hasattr(bloco, "get"):
                        val = bloco.get(key)
                        if val: break
            except Exception: pass
        if val: return _limpar_chave(str(val))
    except Exception: pass
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))

# ============================================================
# COFRE B2 - ÚNICO - DIRETO NO B2, SEM GITHUB
# ============================================================
DB_FILE = "zeus_chat.db"
COFRE_DB_KEY = "banco/zeus_chat.db"
PASTA_APRENDIZADO_B2 = "aprendizado/"
GITHUB_BRANCH = "main"

@st.cache_resource
def _cofre_ctx():
    return {"lock": threading.RLock(), "cliente": None, "cliente_assinatura": "", "nuvem_ok": False, "inicializado": False, "pendente": False, "ultimo_envio": 0.0, "bloqueado_ate": 0.0, "erro": "", "ultimo_tamanho": 0, "resultado_download": "", "cache_busca": {}, "ultima_migracao": ""}

def _criar_cliente_s3(endpoint: str, key_id: str, app_key: str):
    import boto3
    from botocore.config import Config
    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id, aws_secret_access_key=app_key, region_name=m.group(1) if m else None, config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=10, retries={"max_attempts": 1, "mode": "standard"}))

def _obter_cliente_cofre():
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"): endpoint = "https://" + endpoint
    bucket = _secret("B2_BUCKET", "zeus-cofre-danilo")
    key_id, app_key = _secret("B2_KEY_ID"), _secret("B2_APP_KEY")
    if key_id and app_key:
        ctx = _cofre_ctx()
        assinatura = f"{endpoint}|{bucket}|{key_id}|{len(app_key)}"
        if ctx["cliente"] is None or ctx["cliente_assinatura"] != assinatura:
            ctx["cliente"] = _criar_cliente_s3(endpoint, key_id, app_key)
            ctx["cliente_assinatura"] = assinatura
            ctx["nuvem_ok"] = False
        return ctx["cliente"], bucket
    return None, bucket

def _eh_nao_encontrado(e: Exception) -> bool:
    resp = getattr(e, "response", None) or {}
    codigo = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return codigo in ("NoSuchKey", "404", "NotFound") or status == 404

def _conectar():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sessao ON messages(session_id, id)")
    conn.execute("CREATE TABLE IF NOT EXISTS aprendizado (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT UNIQUE, conteudo TEXT, origem TEXT DEFAULT 'chat', local_atual TEXT DEFAULT 'b2', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apr_chave ON aprendizado(chave)")
    conn.commit()
    return conn

def contar_mensagens() -> int:
    try:
        with contextlib.closing(_conectar()) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except Exception: return 0

def _snapshot_local() -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
    src = _conectar(); dest = sqlite3.connect(tmp)
    try:
        src.backup(dest); dest.close()
        with open(tmp, "rb") as f: return f.read()
    finally:
        src.close()
        with contextlib.suppress(Exception): dest.close()
        with contextlib.suppress(OSError): os.remove(tmp)

def _mesclar_banco(corpo: bytes):
    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        with open(tmp, "wb") as f: f.write(corpo)
        conn = _conectar()
        try:
            conn.execute("ATTACH DATABASE ? AS nuvem", (tmp,))
            if conn.execute("SELECT 1 FROM nuvem.sqlite_master WHERE type='table' AND name='messages'").fetchone():
                conn.execute("INSERT INTO main.messages (session_id, role, content, timestamp) SELECT n.session_id, n.role, n.content, n.timestamp FROM nuvem.messages n WHERE NOT EXISTS (SELECT 1 FROM main.messages m WHERE m.session_id=n.session_id AND m.role=n.role AND m.content=n.content AND m.timestamp IS n.timestamp) ORDER BY n.id")
            if conn.execute("SELECT 1 FROM nuvem.sqlite_master WHERE type='table' AND name='aprendizado'").fetchone():
                conn.execute("INSERT OR IGNORE INTO main.aprendizado (chave, conteudo, origem, local_atual, timestamp) SELECT chave, conteudo, origem, local_atual, timestamp FROM nuvem.aprendizado")
            conn.commit()
            with contextlib.suppress(Exception): conn.execute("DETACH DATABASE nuvem")
        finally: conn.close()
    finally:
        with contextlib.suppress(OSError): os.remove(tmp)

def _baixar_e_mesclar(cliente, bucket) -> str:
    ctx = _cofre_ctx()
    try:
        corpo = cliente.get_object(Bucket=bucket, Key=COFRE_DB_KEY)["Body"].read()
    except Exception as e:
        if _eh_nao_encontrado(e):
            ctx["nuvem_ok"], ctx["resultado_download"] = True, "ausente"; return "ausente"
        raise
    if not corpo:
        ctx["nuvem_ok"], ctx["resultado_download"] = True, "vazio"; return "vazio"
    if not corpo.startswith(b"SQLite format 3\x00"): raise ValueError("objeto na nuvem não é SQLite")
    _mesclar_banco(corpo)
    ctx["nuvem_ok"], ctx["resultado_download"] = True, "mesclado"; return "mesclado"

def baixar_db_cofre() -> bool:
    ctx = _cofre_ctx()
    with ctx["lock"]:
        if ctx["nuvem_ok"]: return True
        try:
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket:
                ctx["erro"] = "Credenciais B2 não encontradas (B2_KEY_ID / B2_APP_KEY)."; return False
            _baixar_e_mesclar(cliente, bucket); return True
        except Exception as e:
            ctx["erro"] = f"{type(e).__name__}: {repr(e)}"; return False

def sincronizar_db_cofre(forcar: bool = True) -> bool:
    ctx = _cofre_ctx()
    with ctx["lock"]:
        agora = time.time()
        try:
            if not forcar:
                if agora < ctx["bloqueado_ate"]: ctx["pendente"] = True; return False
                if agora - ctx["ultimo_envio"] < 2: ctx["pendente"] = True; return False
            _conectar().close()
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket:
                ctx["erro"] = "Credenciais B2 não encontradas."; ctx["bloqueado_ate"] = agora + 5; return False
            _baixar_e_mesclar(cliente, bucket)
            corpo = _snapshot_local()
            cliente.put_object(Bucket=bucket, Key=COFRE_DB_KEY, Body=corpo, ContentType="application/octet-stream")
            ctx.update(ultimo_envio=time.time(), pendente=False, erro="", ultimo_tamanho=len(corpo), bloqueado_ate=0.0)
            return True
        except Exception as e:
            ctx["erro"] = f"{type(e).__name__}: {repr(e)}"; ctx["bloqueado_ate"] = time.time() + 5; ctx["pendente"] = True; return False

def salvar_arquivo_cofre(chave_remota: str, dados_ou_caminho) -> bool:
    try:
        cliente, bucket = _obter_cliente_cofre()
        if not cliente or not bucket: return False
        if isinstance(dados_ou_caminho, bytes): corpo = dados_ou_caminho
        elif isinstance(dados_ou_caminho, str) and os.path.exists(dados_ou_caminho):
            with open(dados_ou_caminho, "rb") as f: corpo = f.read()
        else: corpo = str(dados_ou_caminho).encode("utf-8")
        cliente.put_object(Bucket=bucket, Key=chave_remota, Body=corpo); return True
    except Exception as e:
        _log.warning("salvar_arquivo_cofre: %s", repr(e)); return False

# --- ZEUS APRENDE DIRETO NO B2 ---
def zeus_buscar_conhecimento(pergunta: str):
    ctx = _cofre_ctx(); q = pergunta.lower().strip()
    if q in ctx["cache_busca"]: return ctx["cache_busca"][q]
    try:
        with contextlib.closing(_conectar()) as conn:
            rows = conn.execute("SELECT conteudo FROM aprendizado WHERE chave LIKE ? OR conteudo LIKE ? ORDER BY id DESC LIMIT 1", (f"%{q[:40]}%", f"%{q[:40]}%")).fetchall()
            if rows: ctx["cache_busca"][q] = rows[0][0]; return rows[0][0]
    except Exception: pass
    try:
        if not ctx["nuvem_ok"]:
            baixar_db_cofre()
            with contextlib.closing(_conectar()) as conn:
                rows = conn.execute("SELECT conteudo FROM aprendizado WHERE conteudo LIKE ? LIMIT 1", (f"%{q[:40]}%",)).fetchall()
                if rows: ctx["cache_busca"][q] = rows[0][0]; return rows[0][0]
        cliente, bucket = _obter_cliente_cofre()
        if cliente:
            pag = cliente.list_objects_v2(Bucket=bucket, Prefix=PASTA_APRENDIZADO_B2)
            for obj in pag.get("Contents", []):
                if q[:20] and q[:20] in obj["Key"].lower():
                    corpo = cliente.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode("utf-8", errors="ignore")
                    ctx["cache_busca"][q] = corpo; return corpo
    except Exception as e: _log.warning("zeus_buscar: %s", repr(e))
    return None

def zeus_salvar_aprendizado(chave: str, conteudo: str, origem: str = "chat"):
    # DIRETO NO B2 - sem GitHub, sem esperar 03:00
    try:
        chave_limpa = re.sub(r"[^a-z0-9_-]+", "_", chave.lower().strip())[:80] or "apr"
        with contextlib.closing(_conectar()) as conn:
            conn.execute("INSERT OR REPLACE INTO aprendizado (chave, conteudo, origem, local_atual) VALUES (?, ?, ?, 'b2')", (chave_limpa, conteudo, origem))
            conn.commit()
        # 1. Salva arquivo solto no B2
        salvar_arquivo_cofre(f"{PASTA_APRENDIZADO_B2}{chave_limpa}.txt", conteudo)
        # 2. Sobe o banco inteiro
        sincronizar_db_cofre(forcar=True)
        _cofre_ctx()["cache_busca"].clear()
        return True
    except Exception as e:
        _log.warning("zeus_salvar: %s", repr(e)); return False

def zeus_responder_com_memoria(pergunta: str):
    achado = zeus_buscar_conhecimento(pergunta)
    if achado: return f"[Zeus - Lembrei do Cofre B2] {achado}"
    return None

def init_db():
    try:
        _conectar().close()
        ctx = _cofre_ctx()
        if not ctx["inicializado"]:
            if sincronizar_db_cofre(forcar=True): ctx["inicializado"] = True
        elif ctx["pendente"]: sincronizar_db_cofre(forcar=True)
    except Exception as e: _log.warning("init_db: %s", repr(e))

def add_message(session_id, role, content):
    try:
        with contextlib.closing(_conectar()) as conn:
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content)); conn.commit()
    except Exception as e: _log.warning("add_message: %s", repr(e)); return
    with contextlib.suppress(Exception): sincronizar_db_cofre(forcar=True)

def get_recent_context(session_id, limit=6):
    try:
        if not _cofre_ctx()["nuvem_ok"]: baixar_db_cofre()
        with contextlib.closing(_conectar()) as conn:
            rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception: return []

def diagnostico_cofre():
    itens = []
    def add(nome, ok, det=""): itens.append((nome, bool(ok), det))
    try:
        import boto3; add("Pacote boto3", True, boto3.__version__)
    except ImportError: add("Pacote boto3", False, "não instalado - coloque boto3 no requirements.txt")
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com"); bucket = _secret("B2_BUCKET", "zeus-cofre-danilo")
    key_id, app_key = _secret("B2_KEY_ID"), _secret("B2_APP_KEY")
    add("B2_KEY_ID", bool(key_id), f"{key_id[:6]}… ({len(key_id)} caracteres)" if key_id else "não encontrado")
    add("B2_APP_KEY", bool(app_key), f"{len(app_key)} caracteres" if app_key else "não encontrado")
    add("B2_ENDPOINT", bool(endpoint), endpoint); add("B2_BUCKET", bool(bucket), bucket)
    try:
        cliente, _ = _obter_cliente_cofre()
        if cliente:
            cliente.head_bucket(Bucket=bucket); add("Acesso ao bucket", True, "ok")
            try:
                tam = cliente.head_object(Bucket=bucket, Key=COFRE_DB_KEY).get("ContentLength", 0)
                add(f"Arquivo {COFRE_DB_KEY}", tam > 0, f"{tam} bytes" if tam else "0 bytes")
            except Exception as e: add(f"Arquivo {COFRE_DB_KEY}", False, "ainda não existe" if _eh_nao_encontrado(e) else f"{type(e).__name__}")
    except Exception as e: add("Acesso ao bucket", False, f"{type(e).__name__}: {str(e)[:150]}")
    add("Banco local", os.path.exists(DB_FILE), f"{os.path.getsize(DB_FILE)} bytes · {contar_mensagens()} mensagens" if os.path.exists(DB_FILE) else "ainda não criado")
    ctx = _cofre_ctx()
    if ctx["erro"]: add("Último erro", False, ctx["erro"])
    with contextlib.suppress(Exception):
        with contextlib.closing(_conectar()) as conn:
            no_b2 = conn.execute("SELECT COUNT(*) FROM aprendizado WHERE local_atual='b2'").fetchone()[0]
            add("Aprendizados no B2", True, f"{no_b2} arquivos")
    return itens

def mostrar_diagnostico_cofre():
    with st.expander("🔧 Diagnóstico do Cofre B2", expanded=False):
        if st.button("Testar envio agora", key="cofre_diag_btn", use_container_width=True):
            ok = sincronizar_db_cofre(forcar=True)
            if ok: st.success(f"Envio concluído ({_cofre_ctx()['ultimo_tamanho']} bytes).")
            else: st.error("Envio falhou, veja abaixo.")
            for nome, ok_, det in diagnostico_cofre(): st.write(f"{'✅' if ok_ else '❌'} **{nome}** — {det}")
        else: st.caption("Toque no botão para testar a conexão e ver o motivo exato de qualquer falha.")

# ============================================================
# LLM - GROQ, GEMINI, CLAUDE, ETC - SEM MUDAR NOMES
# ============================================================
SYS_PROMPT = ("Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
"Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
"Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico quando preciso. "
"Você evolui continuamente: aprende com outras IAs, com a web e com o usuário. "
"Quando as IAs falham, você busca na internet por conta própria e responde com o que encontrar. "
"Você NUNCA fala de socket, terminal, daemon, b2 file upload, ou que não tem acesso. Você usa boto3 via Streamlit. "
"Você NUNCA inventa métricas. Se não conseguir salvar, fale o erro real do diagnóstico. "
"Você NUNCA pede para o usuário rodar comando no terminal. "
"Responda em português. Seja objetivo.")
PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY", "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL", "huggingface": "HF_MODEL", "github": "GITHUB_MODEL", "deepinfra": "DEEPINFRA_MODEL", "openrouter": "OPENROUTER_MODEL"}
MODELS = {"groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"], "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"], "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5"], "huggingface": ["meta-llama/Llama-3.3-70B-Instruct"], "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"], "deepinfra": ["cognitivecomputations/dolphin-2.9-llama3-8b", "meta-llama/Llama-3.3-70B-Instruct"], "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini"]}
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
            if code in TRANSIENT and attempt == 0: time.sleep(1.5); continue
            break
        if "credit balance" in last.lower() or code in (401, 403, 429): break
        if code in (400, 404) + TRANSIENT: continue
        break
    return False, last
def _call_groq(api_key, model, msgs, system=None):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}, timeout=60)
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
        parts = r.json()["candidates"][0]["content"]["parts"]; return 200, "".join(p.get("text", "") for p in parts)
    return r.status_code, r.text
def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers: headers.update(extra_headers)
    r = requests.post(url, headers=headers, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}, timeout=60)
    if r.status_code == 200: return 200, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return r.status_code, r.text
def _call_huggingface(api_key, model, msgs, system=None): return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", api_key, model, msgs, system)
def _call_github(api_key, model, msgs, system=None): return _call_openai_compat("https://models.github.ai/inference/chat/completions", api_key, model, msgs, system, {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=api_key, base_url=base, timeout=60)
            resp = client.chat.completions.create(model=model, temperature=0.4, max_tokens=700, messages=[{"role": "system", "content": system or SYS_PROMPT}] + msgs)
            return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e: return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)
def _call_openrouter(api_key, model, msgs, system=None): return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", api_key, model, msgs, system, {"X-Title": "Zeus 3.0"})
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
    if "high demand" in low or "unavailable" in low: return "alta demanda, tente de novo"
    return text[:120]
def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}])
    order = [model_choice] + [p for p in PROVIDERS if p != model_choice]; erros = []
    for prov in order:
        api_key = _secret(KEYS[prov])
        if not api_key: erros.append(f"{prov}: chave ausente"); continue
        override = _secret(MODEL_SECRET[prov]); candidates = [override] if override else MODELS[prov]
        try: ok, text = _try_models(candidates, lambda m: CALLERS[prov](api_key, m, msgs, system))
        except Exception as e: ok, text = False, f"erro de conexão: {e}"
        if ok: return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

# ============================================================
# BUSCA WEB
# ============================================================
WEB_TRIGGERS = ("hoje", "agora", "atual", "notícia", "recente", "pesquise", "busque", "procure", "google", "internet", "cotação", "dólar", "preço", "clima", "previsão", "resultado", "placar", "lançamento", "ao vivo")
def _search_google_cse(q, n=5):
    key, cx = _secret("GOOGLE_API_KEY"), _secret("GOOGLE_CSE_ID")
    if not (key and cx): return ""
    r = requests.get("https://www.googleapis.com/customsearch/v1", params={"key": key, "cx": cx, "q": q, "num": n, "hl": "pt"}, timeout=20)
    if r.status_code != 200: return ""
    return "\n".join("- " + i.get("title", "") + ": " + i.get("snippet", "") + " (" + i.get("link", "") + ")" for i in r.json().get("items", []))
def _search_ddg(q, n=5):
    try:
        try: from ddgs import DDGS
        except ImportError: from duckduckgo_search import DDGS
        with DDGS() as d: res = list(d.text(q, max_results=n))
        return "\n".join("- " + x.get("title", "") + ": " + x.get("body", "") for x in res)
    except Exception: return ""
def _search_wiki(q, n=4):
    try:
        r = requests.get("https://pt.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n}, timeout=20)
        return "\n".join("- " + i["title"] + ": " + re.sub(r"<[^>]+>", "", i["snippet"]) for i in r.json()["query"]["search"])
    except Exception: return ""
def web_search(q, n=5):
    for nome, fn in (("Google CSE", _search_google_cse), ("DuckDuckGo", _search_ddg), ("Wikipédia", _search_wiki)):
        try: txt = fn(q)
        except Exception: txt = ""
        if txt and str(txt).strip(): return str(txt).strip(), nome
    return "", ""
def precisa_web(prompt):
    modo = st.session_state.get("web_mode", "Automática")
    if modo == "Desligada": return False
    if modo == "Sempre": return True
    low = prompt.lower(); return any(t in low for t in WEB_TRIGGERS) or bool(re.search(r"\b20(2[4-9]|3[0-9])\b", low))
def enriquecer_com_web(prompt):
    if not precisa_web(prompt): return prompt, ""
    ctx_web, fonte = web_search(prompt[:300])
    if not ctx_web: return prompt, ""
    hoje = time.strftime("%d/%m/%Y")
    return ("[DADOS DA INTERNET em " + hoje + ", via " + fonte + "]\n" + ctx_web[:3500] + "\n[FIM]\n\nPergunta: " + prompt), fonte

# ============================================================
# TELEGRAM - MANDA E RESPONDE, MESMO COM CHAT FECHADO
# ============================================================
def _telegram_creds():
    token = _secret("TELEGRAM_BOT_TOKEN") or _secret("TELEGRAM_TOKEN") or ""
    if not token:
        try:
            bloco = st.secrets.get("telegram", {})
            if hasattr(bloco, "get"): token = bloco.get("token", "") or bloco.get("bot_token", "")
        except Exception: pass
    chat_id = _secret("TELEGRAM_CHAT_ID") or ""
    if not chat_id:
        try:
            bloco = st.secrets.get("telegram", {})
            if hasattr(bloco, "get"): chat_id = str(bloco.get("chat_id", "") or "")
        except Exception: pass
    return _limpar_chave(token), _limpar_chave(str(chat_id))

def zf_enviar_telegram(texto: str, chat_id_destino=None) -> str:
    token, chat_padrao = _telegram_creds()
    chat_id = chat_id_destino or chat_padrao
    if not token or not chat_id: return "❌ Token do Telegram ou Chat ID não encontrados no Secrets."
    texto = (texto or "").strip() or "Teste Zeus - B2 conectado."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}, timeout=30)
        if r.status_code == 200 and r.json().get("ok"): return "✅ Mensagem enviada no Telegram!"
        return f"❌ Telegram ({r.status_code}): {r.text[:200]}"
    except Exception as e: return f"❌ Erro Telegram: {e}"

def _telegram_get_updates(offset=None):
    token, _ = _telegram_creds()
    if not token: return []
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"timeout": 5, "offset": offset} if offset else {"timeout": 5}, timeout=15)
        if r.status_code == 200: return r.json().get("result", [])
    except Exception: pass
    return []

def zeus_responder_texto(session_id, prompt: str):
    # 1. Busca no B2 primeiro - sem outra IA
    mem = zeus_responder_com_memoria(prompt)
    if mem: return mem
    # 2. Enriquece com web se precisar
    prompt_llm, _ = enriquecer_com_web(prompt)
    ok, resp, prov, erros = get_llm_response(session_id, prompt_llm, "groq")
    if not ok:
        ctx_web, fonte = web_search(prompt[:500])
        if ctx_web: resp = "📡 As IAs não responderam. Busquei na internet:\n\n" + ctx_web[:3500] + f"\n\n(Fonte: {fonte})"; ok = True
        else: resp = "Não consegui contatar as IAs nem a web agora. Tente de novo."
    # 3. Aprende e salva direto no B2
    if ok and len(resp) > 20:
        try: zeus_salvar_aprendizado(prompt[:40], resp[:3000], origem="chat")
        except Exception: pass
    return resp

def _telegram_loop():
    offset = None
    vistos = set()
    while True:
        try:
            updates = _telegram_get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                texto = (msg.get("text") or "").strip()
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id", ""))
                de = msg.get("from", {})
                if not texto or not chat_id: continue
                # Evita responder a si mesmo / loop
                if de.get("is_bot"): continue
                chave_dup = f"{chat_id}:{texto[:80]}:{msg.get('date','')}"
                if chave_dup in vistos: continue
                vistos.add(chave_dup)
                if len(vistos) > 200: vistos.clear()
                sid = f"telegram_{chat_id}"
                # Comando direto de envio
                low = texto.lower()
                if low.startswith("zeus manda") or "manda no telegram" in low:
                    continue
                # Responde no Telegram, mesmo com Streamlit fechado
                try:
                    add_message(sid, "user", texto)
                    resp = zeus_responder_texto(sid, texto)
                    add_message(sid, "assistant", resp)
                    zf_enviar_telegram(resp[:4000], chat_id_destino=chat_id)
                except Exception as e: _log.warning("telegram_loop: %s", repr(e))
            time.sleep(3)
        except Exception:
            time.sleep(5)

# Inicia loop do Telegram UMA vez
if "_telegram_thread_started" not in st.session_state:
    st.session_state["_telegram_thread_started"] = True
    threading.Thread(target=_telegram_loop, daemon=True).start()

# ============================================================
# VOZ - Whisper + Edge TTS
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    api_key = _secret("GROQ_API_KEY")
    if not api_key: return "", "GROQ_API_KEY ausente."
    if not audio_bytes: return "", "Áudio vazio."
    base = (mime or "audio/webm").split(";")[0]; ext = "mp4" if "mp4" in base else "ogg" if "ogg" in base else "webm"
    try:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {api_key}"}, files={"file": (f"audio.{ext}", audio_bytes, base)}, data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0"}, timeout=60)
        if r.status_code != 200: return "", f"Transcrição falhou ({r.status_code})"
        return r.json().get("text", "").strip(), ""
    except Exception as e: return "", f"Erro: {e}"

def _limpar_para_fala(texto: str, limite: int = 700) -> str:
    texto = re.sub(r"```.*?```", " (código omitido) ", texto, flags=re.S); texto = re.sub(r"[*#`_>~|]", "", texto).strip()
    if len(texto) > limite:
        corte = texto[:limite]; fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? ")); texto = corte[: fim + 1] if fim > 200 else corte
    return texto
async def _edge_synth(texto, voz, rate):
    import edge_tts; chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
        if c["type"] == "audio": chunks.append(c["data"])
    return b"".join(chunks)
def sintetizar(texto, voz, rate=1.0):
    texto = _limpar_para_fala(texto)
    if not texto: return None
    pct = f"{int(round((rate - 1) * 100)):+d}%"
    try: return asyncio.run(asyncio.wait_for(_edge_synth(texto, voz, pct), timeout=25)) or None
    except Exception as e: st.session_state.tts_erro = str(e)[:150]; return None

# ============================================================
# PROCESSAR PROMPT - CHAT PRINCIPAL
# ============================================================
def processar_prompt(prompt):
    sid = st.session_state.session_id
    # Comando Telegram direto do chat
    low = prompt.lower()
    if "telegram" in low and ("manda" in low or "envia" in low or "enviar" in low):
        t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*", "", prompt).strip(" .,!?:-") or "Alerta Zeus disparado por Danilo."
        resp = zf_enviar_telegram(t)
        st.session_state.messages.append({"role": "user", "content": prompt}); add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(sid, "assistant", resp)
        st.session_state.last_info = "⚡ Enviado no Telegram"; return resp
    st.session_state.messages.append({"role": "user", "content": prompt}); add_message(sid, "user", prompt)
    resp = zeus_responder_texto(sid, prompt)
    st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(sid, "assistant", resp)
    st.session_state.last_info = "✅ Zeus respondeu (B2 primeiro, depois IA)"
    return resp

# ============================================================
# INTERFACE - MESMO PAINEL
# ============================================================
def main():
    inject_theme("Cyberpunk")
    if "session_id" not in st.session_state: st.session_state.session_id = "zeus_web_1"
    if "messages" not in st.session_state: st.session_state.messages = []
    if "model_sel" not in st.session_state: st.session_state.model_sel = "groq"
    if "web_mode" not in st.session_state: st.session_state.web_mode = "Automática"
    init_db()
    st.title("⚡ Zeus 3.0")
    st.caption("B2 direto · Aprende e busca no B2 · Telegram 24h mesmo com chat fechado")
    with st.sidebar:
        st.header("⚙️ Zeus")
        st.session_state.model_sel = st.selectbox("Modelo principal", PROVIDERS, index=0)
        st.session_state.web_mode = st.selectbox("Busca na web", ["Automática", "Sempre", "Desligada"])
        st.text_input("Session ID", key="session_id")
        if st.button("🧹 Limpar conversa"): st.session_state.messages = []; st.rerun()
        mostrar_diagnostico_cofre()
        if st.button("📩 Testar Telegram"):
            st.info(zf_enviar_telegram("✅ Teste Zeus: B2 conectado e Telegram respondendo."))
        st.markdown("---")
        st.caption("Manda 'oi' no Telegram que ele responde lá, mesmo sem o chat aberto.")
    for m in st.session_state.messages:
        with st.chat_message(m["role"]): st.markdown(m["content"])
    if st.session_state.get("last_info"): st.caption(st.session_state["last_info"])
    # Áudio
    audio = st.audio_input("🎙️ Falar com Zeus")
    if audio is not None:
        texto, erro = transcrever_audio(audio.getvalue(), audio.type)
        if texto: processar_prompt(texto); st.rerun()
        elif erro: st.warning(erro)
    prompt = st.chat_input("Fala com o Zeus...")
    if prompt:
        processar_prompt(prompt); st.rerun()
    # Voz da última resposta
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        if st.button("🔊 Ouvir última resposta"):
            audio_out = sintetizar(st.session_state.messages[-1]["content"], "pt-BR-FranciscaNeural")
            if audio_out: st.audio(audio_out, format="audio/mp3")

if __name__ == "__main__":
    main()
