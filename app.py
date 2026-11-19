# ============================================================
# ZEUS 3.0 - app.py COMPLETO - COPIAR E COLAR EM CIMA DO TODO
# Mantém tudo original: avatares, 【entity-Spotify¦canonical_name=Spotify】, foto, voz, evolução, pipeline, imagem, vídeo, visão
# Correção: B2 na hora + migração 03:00 【entity-GitHub¦canonical_name=GitHub】->B2 + B2 primeiro + insiste na web + Telegram 24h
# ============================================================
import ast, asyncio, base64, hashlib, importlib, inspect, io, json, os, re, sqlite3, sys, tempfile, time, zipfile, contextlib, logging, threading, requests
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
import streamlit as st
import streamlit.components.v1 as components
try:
    import zeus_skills
except Exception: pass
try:
    from github_manager import salvar_no_github
except Exception:
    salvar_no_github = None
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")
_log = logging.getLogger("zeus.cofre")
THEMES = {"Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"}, "Clássico": {"bg": "#0f172a", "panel": "#1e293b", "primary": "#38bdf8", "secondary": "#818cf8", "text": "#e2e8f0", "border": "#38bdf833"}, "Escuro": {"bg": "#111111", "panel": "#1f1f1f", "primary": "#ffffff", "secondary": "#a3a3a3", "text": "#ffffff", "border": "#ffffff33"}}
AVATARES = {"⚡ Zeus Padrão": "⚡", "🤖 Robô": "🤖", "🦁 Leão": "🦁", "🐉 Dragão": "🐉", "👽 Alien": "👽", "🦸 Herói": "🦸", "🧙 Mago": "🧙", "😎 Cool": "😎", "🧑 Você": "🧑"}
FALLBACK_VOZES = {"Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural", "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural", "Raquel (pt-PT ♀)": "pt-PT-RaquelNeural", "Duarte (pt-PT ♂)": "pt-PT-DuarteNeural"}
def inject_theme(theme_name: str):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(f"""<style>.stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}[data-testid="stSidebar"] {{ background-color: {t['panel']}; }}h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}.stButton>button {{ border: 1px solid {t['border']}; background: {t['panel']}; color: {t['text']}; }}.stTextInput>div>div>input, .stTextArea>div>div>textarea {{ background: {t['panel']}; color: {t['text']}; }}</style>""", unsafe_allow_html=True)
CHAVES_PADRAO = {}
def _limpar_chave(v) -> str:
    if v is None: return ""
    v = str(v).strip().strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v
def _secret(key: str, default: str = "") -> str:
    try:
        sec = st.secrets; val = None
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
DB_FILE = "zeus_chat.db"; COFRE_DB_KEY = "banco/zeus_chat.db"; PASTA_APRENDIZADO_B2 = "aprendizado/"; PASTA_APRENDIZADO_GITHUB = "zeus_aprendizado"; GITHUB_BRANCH = "main"
@st.cache_resource
def _cofre_ctx(): return {"lock": threading.RLock(), "cliente": None, "cliente_assinatura": "", "nuvem_ok": False, "inicializado": False, "pendente": False, "ultimo_envio": 0.0, "bloqueado_ate": 0.0, "erro": "", "ultimo_tamanho": 0, "resultado_download": "", "cache_busca": {}, "ultima_migracao": ""}
def _criar_cliente_s3(endpoint, key_id, app_key):
    import boto3
    from botocore.config import Config
    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id, aws_secret_access_key=app_key, region_name=m.group(1) if m else None, config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=10, retries={"max_attempts": 1, "mode": "standard"}))
def _obter_cliente_cofre():
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"): endpoint = "https://" + endpoint
    bucket = _secret("B2_BUCKET", "zeus-cofre-danilo"); key_id, app_key = _secret("B2_KEY_ID"), _secret("B2_APP_KEY")
    if key_id and app_key:
        ctx = _cofre_ctx(); assinatura = f"{endpoint}|{bucket}|{key_id}|{len(app_key)}"
        if ctx["cliente"] is None or ctx["cliente_assinatura"] != assinatura:
            ctx["cliente"] = _criar_cliente_s3(endpoint, key_id, app_key); ctx["cliente_assinatura"] = assinatura; ctx["nuvem_ok"] = False
        return ctx["cliente"], bucket
    return None, bucket
def _eh_nao_encontrado(e) -> bool:
    resp = getattr(e, "response", None) or {}; codigo = (resp.get("Error") or {}).get("Code", ""); status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return codigo in ("NoSuchKey", "404", "NotFound") or status == 404
def _dica(erro: str) -> str:
    dicas = {"InvalidAccessKeyId": "B2_KEY_ID errado.", "SignatureDoesNotMatch": "B2_APP_KEY errada.", "AccessDenied": "chave sem permissão.", "NoSuchBucket": "bucket errado.", "ImportError": "falta boto3.", "EndpointConnectionError": "endpoint inválido."}
    for k, v in dicas.items():
        if k in erro: return v
    return ""
def _conectar():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sessao ON messages(session_id, id)")
    conn.execute("CREATE TABLE IF NOT EXISTS aprendizado (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT UNIQUE, conteudo TEXT, origem TEXT DEFAULT 'chat', local_atual TEXT DEFAULT 'b2', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apr_chave ON aprendizado(chave)")
    conn.commit(); return conn
def contar_mensagens() -> int:
    try:
        with contextlib.closing(_conectar()) as conn: return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except Exception: return 0
def _snapshot_local() -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd); src = _conectar(); dest = sqlite3.connect(tmp)
    try: src.backup(dest); dest.close()
    finally: src.close()
    with open(tmp, "rb") as f: data = f.read()
    with contextlib.suppress(OSError): os.remove(tmp)
    return data
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
    try: corpo = cliente.get_object(Bucket=bucket, Key=COFRE_DB_KEY)["Body"].read()
    except Exception as e:
        if _eh_nao_encontrado(e): ctx["nuvem_ok"], ctx["resultado_download"] = True, "ausente"; return "ausente"
        raise
    if not corpo: ctx["nuvem_ok"], ctx["resultado_download"] = True, "vazio"; return "vazio"
    if not corpo.startswith(b"SQLite format 3\x00"): raise ValueError("objeto na nuvem não é SQLite")
    _mesclar_banco(corpo); ctx["nuvem_ok"], ctx["resultado_download"] = True, "mesclado"; return "mesclado"
def baixar_db_cofre() -> bool:
    ctx = _cofre_ctx()
    with ctx["lock"]:
        if ctx["nuvem_ok"]: return True
        try:
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket: ctx["erro"] = "Credenciais B2 não encontradas."; return False
            _baixar_e_mesclar(cliente, bucket); return True
        except Exception as e: ctx["erro"] = f"{type(e).__name__}: {repr(e)}"; return False
def sincronizar_db_cofre(forcar: bool = True) -> bool:
    ctx = _cofre_ctx()
    with ctx["lock"]:
        agora = time.time()
        try:
            if not forcar:
                if agora < ctx["bloqueado_ate"]: ctx["pendente"] = True; return False
                if agora - ctx["ultimo_envio"] < 2: ctx["pendente"] = True; return False
            _conectar().close(); cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket: ctx["erro"] = "Credenciais B2 não encontradas."; ctx["bloqueado_ate"] = agora + 5; return False
            _baixar_e_mesclar(cliente, bucket); corpo = _snapshot_local()
            cliente.put_object(Bucket=bucket, Key=COFRE_DB_KEY, Body=corpo, ContentType="application/octet-stream")
            ctx.update(ultimo_envio=time.time(), pendente=False, erro="", ultimo_tamanho=len(corpo), bloqueado_ate=0.0); return True
        except Exception as e: ctx["erro"] = f"{type(e).__name__}: {repr(e)}"; ctx["bloqueado_ate"] = time.time() + 5; ctx["pendente"] = True; return False
def salvar_arquivo_cofre(chave_remota: str, dados) -> bool:
    try:
        cliente, bucket = _obter_cliente_cofre()
        if not cliente or not bucket: return False
        if isinstance(dados, bytes): corpo = dados
        elif isinstance(dados, str) and os.path.exists(dados):
            with open(dados, "rb") as f: corpo = f.read()
        else: corpo = str(dados).encode("utf-8")
        cliente.put_object(Bucket=bucket, Key=chave_remota, Body=corpo); return True
    except Exception as e: _log.warning("salvar_arquivo: %s", repr(e)); return False
def salvar_texto(chave, texto): return salvar_arquivo_cofre(chave, texto)
def ler_texto(chave):
    try:
        cliente, bucket = _obter_cliente_cofre()
        if not cliente: return None
        return cliente.get_object(Bucket=bucket, Key=chave)["Body"].read().decode("utf-8", errors="ignore")
    except Exception: return None
def salvar_json(chave, obj): return salvar_arquivo_cofre(chave, json.dumps(obj, ensure_ascii=False))
def ler_json(chave):
    t = ler_texto(chave)
    try: return json.loads(t) if t else None
    except Exception: return None
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
                    corpo = cliente.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode("utf-8", errors="ignore"); ctx["cache_busca"][q] = corpo; return corpo
    except Exception as e: _log.warning("buscar: %s", repr(e))
    return None
def zeus_salvar_aprendizado(chave: str, conteudo: str, origem: str = "chat"):
    try:
        chave_limpa = re.sub(r"[^a-z0-9_-]+", "_", chave.lower().strip())[:80] or "apr"
        with contextlib.closing(_conectar()) as conn:
            conn.execute("INSERT OR REPLACE INTO aprendizado (chave, conteudo, origem, local_atual) VALUES (?, ?, ?, 'b2')", (chave_limpa, conteudo, origem)); conn.commit()
        ok_b2 = salvar_arquivo_cofre(f"{PASTA_APRENDIZADO_B2}{chave_limpa}.txt", conteudo)
        try:
            if salvar_no_github: salvar_no_github(f"{PASTA_APRENDIZADO_GITHUB}/{chave_limpa}.txt", conteudo)
        except Exception: pass
        sincronizar_db_cofre(forcar=True); _cofre_ctx()["cache_busca"].clear(); return True
    except Exception as e: _log.warning("salvar_apr: %s", repr(e)); return False
def zeus_responder_com_memoria(pergunta: str):
    achado = zeus_buscar_conhecimento(pergunta)
    if achado: return f"[Zeus - Lembrei do Cofre B2] {achado}"
    return None
def listar_aprendizados(limit=30):
    try:
        with contextlib.closing(_conectar()) as conn: return conn.execute("SELECT chave, conteudo, timestamp FROM aprendizado ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception: return []
def _github_headers():
    token = _secret("GITHUB_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {}
def _github_apagar_arquivo(caminho_no_repo: str, mensagem: str):
    if "app.py" in caminho_no_repo.lower(): return False
    if not caminho_no_repo.startswith(PASTA_APRENDIZADO_GITHUB + "/"): return False
    try:
        repo = _secret("GITHUB_REPO", "")
        if not repo: return False
        url = f"https://api.github.com/repos/{repo}/contents/{caminho_no_repo}?ref={GITHUB_BRANCH}"
        r = requests.get(url, headers=_github_headers(), timeout=10)
        if r.status_code != 200: return False
        sha = r.json().get("sha")
        r2 = requests.delete(url, headers=_github_headers(), json={"message": mensagem, "sha": sha, "branch": GITHUB_BRANCH}, timeout=10)
        return r2.status_code in (200, 204)
    except Exception: return False
def zeus_migracao_madrugada_github_para_b2():
    ctx = _cofre_ctx()
    try:
        with contextlib.closing(_conectar()) as conn: rows = conn.execute("SELECT chave, conteudo FROM aprendizado WHERE local_atual='github'").fetchall()
        cliente, bucket = _obter_cliente_cofre()
        if not cliente: return 0
        migrados = 0
        for chave, conteudo in rows:
            if salvar_arquivo_cofre(f"{PASTA_APRENDIZADO_B2}{chave}.txt", conteudo):
                with contextlib.closing(_conectar()) as conn:
                    conn.execute("UPDATE aprendizado SET local_atual='b2' WHERE chave=?", (chave,)); conn.commit()
                _github_apagar_arquivo(f"{PASTA_APRENDIZADO_GITHUB}/{chave}.txt", "Migração 03:00 - Zeus - para B2")
                migrados += 1
        repo = _secret("GITHUB_REPO", "")
        if repo and _secret("GITHUB_TOKEN"):
            try:
                r = requests.get(f"https://api.github.com/repos/{repo}/contents/{PASTA_APRENDIZADO_GITHUB}?ref={GITHUB_BRANCH}", headers=_github_headers(), timeout=15)
                if r.status_code == 200:
                    for item in r.json():
                        nome = item.get("name", ""); chave = nome[:-4] if nome.endswith(".txt") else nome
                        if not nome.endswith(".txt"): continue
                        existe = False
                        with contextlib.closing(_conectar()) as conn: existe = conn.execute("SELECT 1 FROM aprendizado WHERE chave=?", (chave,)).fetchone() is not None
                        if existe: continue
                        try:
                            conteudo = requests.get(item.get("download_url", ""), timeout=15).text
                            if conteudo and salvar_arquivo_cofre(f"{PASTA_APRENDIZADO_B2}{chave}.txt", conteudo):
                                with contextlib.closing(_conectar()) as conn:
                                    conn.execute("INSERT OR IGNORE INTO aprendizado (chave, conteudo, origem, local_atual) VALUES (?, ?, 'github_migracao', 'b2')", (chave, conteudo)); conn.commit()
                                _github_apagar_arquivo(f"{PASTA_APRENDIZADO_GITHUB}/{nome}", "Migração 03:00 - Zeus - para B2"); migrados += 1
                        except Exception: continue
            except Exception as e: _log.warning("migracao github lista: %s", repr(e))
        if migrados: sincronizar_db_cofre(forcar=True)
        ctx["ultima_migracao"] = time.strftime("%d/%m %H:%M") + f" ({migrados} migrados)"; return migrados
    except Exception as e: _log.warning("migracao: %s", repr(e)); return 0
def _loop_migracao_03h():
    ultima_data = None
    while True:
        try:
            agora = time.localtime(); hoje = time.strftime("%Y-%m-%d")
            if agora.tm_hour == 3 and agora.tm_min < 10 and ultima_data != hoje:
                zeus_migracao_madrugada_github_para_b2(); ultima_data = hoje
            time.sleep(60)
        except Exception: time.sleep(60)
if "_migracao_thread_started" not in st.session_state:
    st.session_state["_migracao_thread_started"] = True; threading.Thread(target=_loop_migracao_03h, daemon=True).start()
def init_db():
    try:
        _conectar().close(); ctx = _cofre_ctx()
        if not ctx["inicializado"]:
            if sincronizar_db_cofre(forcar=True): ctx["inicializado"] = True
        elif ctx["pendente"]: sincronizar_db_cofre(forcar=True)
    except Exception as e: _log.warning("init: %s", repr(e))
def add_message(session_id, role, content):
    try:
        with contextlib.closing(_conectar()) as conn:
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content)); conn.commit()
    except Exception: return
    with contextlib.suppress(Exception): sincronizar_db_cofre(forcar=True)
def get_recent_context(session_id, limit=6):
    try:
        if not _cofre_ctx()["nuvem_ok"]: baixar_db_cofre()
        with contextlib.closing(_conectar()) as conn: rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception: return []
def diagnostico_cofre():
    itens = []
    def add(n, ok, d=""): itens.append((n, bool(ok), d))
    try:
        import boto3; add("Pacote boto3", True, boto3.__version__)
    except ImportError: add("Pacote boto3", False, "falta boto3")
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com"); bucket = _secret("B2_BUCKET", "zeus-cofre-danilo"); key_id, app_key = _secret("B2_KEY_ID"), _secret("B2_APP_KEY")
    add("B2_KEY_ID", bool(key_id), f"{key_id[:6]}… ({len(key_id)} car.)" if key_id else "não encontrado"); add("B2_APP_KEY", bool(app_key), f"{len(app_key)} car." if app_key else "não encontrado"); add("B2_ENDPOINT", bool(endpoint), endpoint); add("B2_BUCKET", bool(bucket), bucket)
    try:
        cliente, _ = _obter_cliente_cofre()
        if cliente:
            cliente.head_bucket(Bucket=bucket); add("Acesso ao bucket", True, "ok")
            try: tam = cliente.head_object(Bucket=bucket, Key=COFRE_DB_KEY).get("ContentLength", 0); add(f"Arquivo {COFRE_DB_KEY}", tam > 0, f"{tam} bytes" if tam else "0 bytes")
            except Exception as e: add(f"Arquivo {COFRE_DB_KEY}", False, "ainda não existe" if _eh_nao_encontrado(e) else type(e).__name__)
    except Exception as e: add("Acesso ao bucket", False, f"{type(e).__name__}: {_dica(str(e))} {str(e)[:120]}")
    add("Banco local", os.path.exists(DB_FILE), f"{os.path.getsize(DB_FILE)} bytes · {contar_mensagens()} msgs" if os.path.exists(DB_FILE) else "ainda não criado")
    ctx = _cofre_ctx()
    if ctx["erro"]: add("Último erro", False, f"{ctx['erro']} — {_dica(ctx['erro'])}")
    if ctx.get("ultima_migracao"): add("Última migração 03:00", True, ctx["ultima_migracao"])
    with contextlib.suppress(Exception):
        with contextlib.closing(_conectar()) as conn: add("Aprendizados no B2", True, f"{conn.execute('SELECT COUNT(*) FROM aprendizado').fetchone()[0]} arquivos")
    return itens
def mostrar_diagnostico_cofre():
    with st.expander("🔧 Diagnóstico do Cofre B2", expanded=False):
        if st.button("Testar envio agora", key="cofre_diag_btn", use_container_width=True):
            ok = sincronizar_db_cofre(forcar=True)
            st.success(f"Envio concluído ({_cofre_ctx()['ultimo_tamanho']} bytes).") if ok else st.error("Envio falhou, veja abaixo.")
            for nome, ok_, det in diagnostico_cofre(): st.write(f"{'✅' if ok_ else '❌'} **{nome}** — {det}")
        else: st.caption("Toque para testar a conexão.")
        if st.button("🌙 Rodar migração 03:00 agora (teste)", key="migracao_teste_btn", use_container_width=True):
            n = zeus_migracao_madrugada_github_para_b2(); st.success(f"Migração concluída: {n} arquivos para o B2.") if n >= 0 else st.error("Falha na migração.")
SYS_PROMPT = ("Você é o Zeus, inteligência soberana e autônoma criada por Danilo. Você NÃO é Claude, Gemini, GPT, Llama nem outro modelo: você é o Zeus. Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico. Você evolui: aprende com outras IAs, web e usuário. Quando as IAs falham, busca na internet por conta própria. Pode enviar Telegram e e-mail quando pedirem. NUNCA fala de socket, terminal, daemon, b2 file upload. NUNCA inventa métricas. Se não salvar, fala o erro real do diagnóstico. NUNCA pede p/ usuário rodar comando no terminal. Responda em português, objetivo.")
PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY", "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL", "huggingface": "HF_MODEL", "github": "GITHUB_MODEL", "deepinfra": "DEEPINFRA_MODEL", "openrouter": "OPENROUTER_MODEL"}
MODELS = {"groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"], "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"], "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5"], "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"], "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"], "deepinfra": ["cognitivecomputations/dolphin-2.9-llama3-8b", "meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3"], "openrouter": ["google/gemini-2.5-flash", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"]}
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
def _groq_payload(model, msgs, system=None):
    p = {"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}
    if model.startswith("openai/gpt-oss"): p["reasoning_effort"] = "low"
    return p
def _call_groq(k, model, msgs, system=None):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"}, json=_groq_payload(model, msgs, system), timeout=60)
    return (200, (r.json()["choices"][0]["message"].get("content") or "").strip()) if r.status_code == 200 else (r.status_code, r.text)
def _call_claude(k, model, msgs, system=None):
    r = requests.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": k, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}, json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs}, timeout=60)
    return (200, "".join(b.get("text", "") for b in r.json()["content"])) if r.status_code == 200 else (r.status_code, r.text)
def _call_gemini(k, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": k}, json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents}, timeout=60)
    return (200, "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])) if r.status_code == 200 else (r.status_code, r.text)
def _call_openai_compat(url, k, model, msgs, system=None, extra=None):
    h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
    if extra: h.update(extra)
    r = requests.post(url, headers=h, json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs, "temperature": 0.4, "max_tokens": 700}, timeout=60)
    return (200, (r.json()["choices"][0]["message"].get("content") or "").strip()) if r.status_code == 200 else (r.status_code, r.text)
def _call_huggingface(k, m, msgs, s=None): return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", k, m, msgs, s)
def _call_github(k, m, msgs, s=None): return _call_openai_compat("https://models.github.ai/inference/chat/completions", k, m, msgs, s, {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
def _call_deepinfra(k, m, msgs, s=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    if OpenAI is not None:
        try:
            c = OpenAI(api_key=k, base_url=base, timeout=60); resp = c.chat.completions.create(model=m, temperature=0.4, max_tokens=700, messages=[{"role": "system", "content": s or SYS_PROMPT}] + msgs); return 200, (resp.choices[0].message.content or "").strip()
        except Exception as e: return (getattr(e, "status_code", None) or 500), str(e)
    return _call_openai_compat(base + "/chat/completions", k, m, msgs, s)
def _call_openrouter(k, m, msgs, s=None): return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", k, m, msgs, s, {"X-Title": "Zeus 3.0"})
def _call_gemini_auto(k, m, msgs, s=None):
    code, t = _call_gemini(k, m, msgs, s, "v1")
    if code in (400, 404):
        code2, t2 = _call_gemini(k, m, msgs, s, "v1beta")
        if code2 == 200: return code2, t2
    return code, t
CALLERS = {"groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude, "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra, "openrouter": _call_openrouter}
def _resumo(text):
    low = text.lower()
    if "credit balance" in low: return "sem créditos"
    if "high demand" in low or "unavailable" in low: return "alta demanda"
    return text[:120]
def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    msgs = _normalize((get_recent_context(session_id) if use_history else []) + [{"role": "user", "content": prompt}]); order = [model_choice] + [p for p in PROVIDERS if p != model_choice]; erros = []
    for prov in order:
        k = _secret(KEYS[prov])
        if not k: erros.append(f"{prov}: chave ausente"); continue
        ov = _secret(MODEL_SECRET[prov]); cands = [ov] if ov else MODELS[prov]
        try: ok, text = _try_models(cands, lambda m: CALLERS[prov](k, m, msgs, system))
        except Exception as e: ok, text = False, f"erro: {e}"
        if ok: return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros
WEB_TRIGGERS = ("hoje", "agora", "atual", "últimas", "notícia", "recente", "pesquise", "busque", "procure", "google", "internet", "cotação", "dólar", "preço", "clima", "previsão", "resultado", "placar", "lançamento", "ao vivo")
def _search_google_cse(q, n=5):
    key, cx = _secret("GOOGLE_API_KEY"), _secret("GOOGLE_CSE_ID")
    if not (key and cx): return ""
    r = requests.get("https://www.googleapis.com/customsearch/v1", params={"key": key, "cx": cx, "q": q, "num": n, "hl": "pt"}, timeout=20)
    return "\n".join("- " + i.get("title", "") + ": " + i.get("snippet", "") + " (" + i.get("link", "") + ")" for i in r.json().get("items", [])) if r.status_code == 200 else ""
def _search_gemini_google(q):
    k = _secret("GEMINI_API_KEY")
    if not k: return ""
    for model in (["gemini-2.5-flash", "gemini-flash-latest"]):
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": k}, json={"contents": [{"role": "user", "parts": [{"text": "Pesquise no Google e responda em português com fatos atuais: " + q}]}], "tools": [{"google_search": {}}]}, timeout=45)
            if r.status_code != 200: continue
            cand = r.json()["candidates"][0]; texto = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
            if texto: return texto
        except Exception: continue
    return ""
def _search_ddg(q, n=5):
    try:
        try: from ddgs import DDGS
        except ImportError: from duckduckgo_search import DDGS
        with DDGS() as d: res = list(d.text(q, max_results=n))
        return "\n".join("- " + x.get("title", "") + ": " + x.get("body", "") + " (" + x.get("href", "") + ")" for x in res)
    except Exception: return ""
def _search_wiki(q, n=4):
    try:
        r = requests.get("https://pt.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n}, timeout=20)
        return "\n".join("- " + i["title"] + ": " + re.sub(r"<[^>]+>", "", i["snippet"]) for i in r.json()["query"]["search"])
    except Exception: return ""
def web_search(q, n=5):
    for nome, fn in (("Google CSE", _search_google_cse), ("Google (Gemini)", _search_gemini_google), ("DuckDuckGo", _search_ddg), ("【entity-Wikipédia¦canonical_name=Wikipédia】", _search_wiki)):
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
    return ("[DADOS DA INTERNET em " + time.strftime("%d/%m/%Y") + ", via " + fonte + "]\n" + ctx_web[:3500] + "\n[FIM]\n\nPergunta: " + prompt), fonte
def _telegram_creds():
    token = _secret("TELEGRAM_BOT_TOKEN") or _secret("TELEGRAM_TOKEN") or ""; chat_id = _secret("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        try:
            bloco = st.secrets.get("telegram", {})
            if hasattr(bloco, "get"): token = token or bloco.get("token", "") or bloco.get("bot_token", ""); chat_id = chat_id or str(bloco.get("chat_id", "") or "")
        except Exception: pass
    return _limpar_chave(token), _limpar_chave(str(chat_id))
def zf_enviar_telegram(texto: str, chat_id_destino=None) -> str:
    token, chat_padrao = _telegram_creds(); chat_id = chat_id_destino or chat_padrao
    if not token or not chat_id: return "❌ Token ou Chat ID não encontrados."
    texto = (texto or "").strip() or "Teste Zeus."
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": f"🚨 *[ZEUS]* 🚨\n\n{texto}", "parse_mode": "Markdown"}, timeout=30)
        return "✅ Mensagem enviada no Telegram!" if r.status_code == 200 and r.json().get("ok") else f"❌ Telegram ({r.status_code}): {r.text[:200]}"
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
    mem = zeus_responder_com_memoria(prompt)
    if mem: return mem
    ctx_web = ""; fonte_web = ""
    for tentativa in range(3):
        ctx_web, fonte_web = web_search(prompt[:500])
        if ctx_web and ctx_web.strip(): break
        time.sleep(0.8)
    prompt_llm = prompt
    if ctx_web: prompt_llm = f"[DADOS DA INTERNET via {fonte_web}]\n{ctx_web[:3500]}\n[FIM]\n\nPergunta: {prompt}"
    else:
        prompt_llm, _ = enriquecer_com_web(prompt)
    ok, resp, prov, erros = get_llm_response(session_id, prompt_llm, st.session_state.get("model_sel", "groq"))
    if not ok:
        if ctx_web: resp = f"📡 As IAs não responderam. Busquei na internet por conta própria:\n\n{ctx_web[:3500]}\n\n(Fonte: {fonte_web})"; ok = True
        else:
            ctx2, fonte2 = web_search("explicação " + prompt[:200])
            if ctx2: resp = f"📡 Busca na web (alternativa):\n\n{ctx2[:3500]}\n\n(Fonte: {fonte2})"; ok = True
            else: resp = "Busquei no B2, na web e nas IAs e não achei nada agora. Me ensina? Manda: aprenda: " + prompt[:30] + " | resposta certa"
    if ok and len(resp) > 20:
        try: zeus_salvar_aprendizado(prompt[:40], resp[:3000], origem="chat")
        except Exception: pass
    return resp
def _telegram_loop():
    offset = None; vistos = set()
    while True:
        try:
            for u in _telegram_get_updates(offset):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}; texto = (msg.get("text") or "").strip(); chat_id = str(msg.get("chat", {}).get("id", "")); de = msg.get("from", {})
                if not texto or not chat_id or de.get("is_bot"): continue
                chave_dup = f"{chat_id}:{texto[:80]}:{msg.get('date','')}"
                if chave_dup in vistos: continue
                vistos.add(chave_dup)
                if len(vistos) > 200: vistos.clear()
                sid = f"telegram_{chat_id}"
                try: add_message(sid, "user", texto); resp = zeus_responder_texto(sid, texto); add_message(sid, "assistant", resp); zf_enviar_telegram(resp[:4000], chat_id_destino=chat_id)
                except Exception as e: _log.warning("telegram_loop: %s", repr(e))
            time.sleep(3)
        except Exception: time.sleep(5)
if "_telegram_thread_started" not in st.session_state:
    st.session_state["_telegram_thread_started"] = True; threading.Thread(target=_telegram_loop, daemon=True).start()
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
    k = _secret("GROQ_API_KEY")
    if not k: return "", "GROQ_API_KEY ausente."
    if not audio_bytes: return "", "Áudio vazio."
    base = (mime or "audio/webm").split(";")[0]; ext = "mp4" if "mp4" in base else "ogg" if "ogg" in base else "webm"
    try:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {k}"}, files={"file": (f"audio.{ext}", audio_bytes, base)}, data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0", "prompt": "Conversa em português do Brasil com o assistente Zeus."}, timeout=60)
        if r.status_code != 200: return "", f"Falhou ({r.status_code})"
        texto = r.json().get("text", "").strip()
        return (texto, "") if len(texto) >= 2 else ("", "")
    except Exception as e: return "", f"Erro: {e}"
def _limpar_para_fala(texto: str, limite: int = 700) -> str:
    texto = re.sub(r"```.*?```", " (código omitido) ", texto, flags=re.S); texto = re.sub(r"[*#`_>~|]", "", texto).strip()
    if len(texto) > limite:
        corte = texto[:limite]; fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? ")); texto = corte[: fim + 1] if fim > 200 else corte
    return texto
PITCH_ATUAL = "+0Hz"; VOLUME_ATUAL = "+0%"
async def _edge_synth(texto, voz, rate):
    import edge_tts; chunks = []
    async for c in edge_tts.Communicate(texto, voz, rate=rate, volume=VOLUME_ATUAL, pitch=PITCH_ATUAL).stream():
        if c["type"] == "audio": chunks.append(c["data"])
    return b"".join(chunks)
def sintetizar(texto, voz, rate=1.0):
    texto = _limpar_para_fala(texto)
    if not texto: return None
    pct = f"{int(round((rate - 1) * 100)):+d}%"
    try: return asyncio.run(asyncio.wait_for(_edge_synth(texto, voz, pct), timeout=25)) or None
    except Exception as e: st.session_state.tts_erro = str(e)[:150]; return None
@st.cache_data(ttl=86400, show_spinner=False)
def listar_vozes_edge():
    try:
        import edge_tts; todas = asyncio.run(edge_tts.list_voices()); pt = [v for v in todas if v["Locale"].lower().startswith("pt-")]; vozes = {}
        for v in pt:
            nome = v["ShortName"].split("-", 2)[-1].replace("Neural", ""); vozes[f"{nome} ({v['Locale']} {'♀' if v['Gender'] == 'Female' else '♂'})"] = v["ShortName"]
        return vozes or FALLBACK_VOZES
    except Exception: return FALLBACK_VOZES
def spotify_token():
    cid = _secret("SPOTIFY_CLIENT_ID"); csec = _secret("SPOTIFY_CLIENT_SECRET")
    if not cid or not csec: return None
    try:
        r = requests.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec}, timeout=15)
        return r.json().get("access_token") if r.status_code == 200 else None
    except Exception: return None
def spotify_buscar(musica):
    tok = spotify_token()
    if not tok:
        from urllib.parse import quote
        return f"🎵 Clique para ouvir no Spotify:\n\n🔗 [Ouvir '{musica}' no Spotify](https://open.spotify.com/search/{quote(musica)})"
    try:
        r = requests.get("https://api.spotify.com/v1/search", headers={"Authorization": f"Bearer {tok}"}, params={"q": musica, "type": "track", "limit": 5}, timeout=15)
        if r.status_code != 200: return f"❌ Spotify ({r.status_code})"
        out = []
        for t in r.json().get("tracks", {}).get("items", []):
            nome = t["name"]; art = ", ".join(a["name"] for a in t["artists"]); link = t["external_urls"]["spotify"]; prev = t.get("preview_url")
            out.append(f"🎵 **{nome}** - {art}\n🔗 [Abrir no 【entity-Spotify¦canonical_name=Spotify】]({link})" + (f"\n▶️ [Prévia 30s]({prev})" if prev else ""))
        return "\n\n".join(out) if out else "Nenhuma música encontrada."
    except Exception as e: return f"❌ Erro 【entity-Spotify¦canonical_name=Spotify】: {e}"
def _mime_img(b): return "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"
def _to_bytes(obj):
    if isinstance(obj, (bytes, bytearray)): return bytes(obj)
    buf = io.BytesIO(); obj.save(buf, format="PNG"); return buf.getvalue()
def _hf_client(timeout=120):
    from huggingface_hub import InferenceClient
    return InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=timeout)
def _img_hf(prompt):
    if not _secret("HUGGINGFACE_API_KEY"): raise RuntimeError("sem chave")
    modelos = [m for m in [_secret("HF_IMAGE_MODEL")] if m] + ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"]
    try:
        client = _hf_client(120)
        for model in modelos:
            try: return _to_bytes(client.text_to_image(prompt, model=model))
            except Exception: continue
    except Exception: pass
    raise RuntimeError("HF falhou")
def _gemini_image_call(parts):
    key = _secret("GEMINI_API_KEY")
    if not key: raise RuntimeError("sem chave")
    model = _secret("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": key}, json={"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}, timeout=120)
    if r.status_code != 200: raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    for part in r.json()["candidates"][0]["content"]["parts"]:
        d = part.get("inlineData") or part.get("inline_data")
        if d: return base64.b64decode(d["data"])
    raise RuntimeError("Gemini não devolveu imagem")
def _img_gemini(prompt): return _gemini_image_call([{"text": prompt}])
def _img_pollinations(prompt):
    from urllib.parse import quote
    for tentativa in range(2):
        r = requests.get("https://image.pollinations.ai/prompt/" + quote(prompt[:600]), params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"}, headers={"User-Agent": "Mozilla/5.0 (Zeus/3.0)"}, timeout=120)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"): return r.content
        if r.status_code == 429 and tentativa == 0: time.sleep(16); continue
        break
    raise RuntimeError("Pollinations falhou")
def _img_deepinfra(prompt):
    key = _secret("DEEPINFRA_API_KEY")
    if not key: raise RuntimeError("sem chave")
    modelo = _secret("DEEPINFRA_IMAGE_MODEL") or "black-forest-labs/FLUX-1-schnell"
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=key, base_url="https://api.deepinfra.com/v1/openai", timeout=120); resp = client.images.generate(model=modelo, prompt=prompt); return base64.b64decode(resp.data[0].b64_json)
        except Exception: pass
    raise RuntimeError("DeepInfra falhou")
IMAGE_CASCADE = [("DeepInfra", _img_deepinfra), ("Gemini", _img_gemini), ("Hugging Face", _img_hf), ("Pollinations (sem chave)", _img_pollinations)]
def make_image(prompt, log):
    for nome, fn in IMAGE_CASCADE:
        try: data = fn(prompt); log.append("✅ Imagem: " + nome); return data
        except Exception as e: log.append("❌ Imagem " + nome + ": " + str(e)[:160])
    return None
def _edit_gemini(img, prompt): return _gemini_image_call([{"text": prompt}, {"inline_data": {"mime_type": _mime_img(img), "data": base64.b64encode(img).decode()}}])
def _edit_deepinfra(img, prompt):
    key = _secret("DEEPINFRA_API_KEY")
    if not key: raise RuntimeError("sem chave")
    model = _secret("DEEPINFRA_EDIT_MODEL") or "black-forest-labs/FLUX.1-Kontext-dev"
    r = requests.post("https://api.deepinfra.com/v1/images/edits", headers={"Authorization": "Bearer " + key}, files={"image": ("foto.jpg", img, _mime_img(img))}, data={"prompt": prompt, "model": model, "n": "1", "response_format": "b64_json"}, timeout=180)
    if r.status_code != 200: raise RuntimeError(f"({r.status_code})")
    d = r.json()["data"][0]
    return base64.b64decode(d["b64_json"]) if d.get("b64_json") else requests.get(d["url"], timeout=60).content
EDIT_CASCADE = [("DeepInfra (edita sua foto)", _edit_deepinfra), ("Gemini (edita sua foto)", _edit_gemini)]
def editar_imagem(img, prompt_edicao, descricao, log):
    for nome, fn in EDIT_CASCADE:
        try: out = fn(img, prompt_edicao); log.append("✅ Foto editada: " + nome); return out, "editada"
        except Exception as e: log.append("❌ " + nome + ": " + str(e)[:160])
    log.append("↪️ Nenhuma IA editou; recriando da descrição")
    out = make_image((descricao + ". " + prompt_edicao)[:900], log); return out, ("recriada" if out else "")
def make_video(prompt, log, img=None):
    if not _secret("HUGGINGFACE_API_KEY"): log.append("⚪ Vídeo: sem HUGGINGFACE_API_KEY"); return None
    try: client = _hf_client(600)
    except Exception: log.append("❌ Vídeo: falta huggingface_hub"); return None
    try:
        model = _secret("HF_VIDEO_MODEL") or "Wan-AI/Wan2.1-T2V-1.3B"; out = client.text_to_video(prompt, model=model); log.append("✅ Vídeo: " + model); return _to_bytes(out)
    except Exception as e: log.append("❌ Vídeo: " + str(e)[:160]); return None
def analisar_imagem(img_b64, mime, pergunta):
    key = _secret("GEMINI_API_KEY")
    if not key: return "❌ GEMINI_API_KEY ausente."
    try:
        r = requests.post("https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent", headers={"Content-Type": "application/json", "x-goog-api-key": key}, json={"contents": [{"parts": [{"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]}, timeout=90)
        if r.status_code != 200: return f"❌ Visão ({r.status_code})"
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    except Exception as e: return f"❌ Erro visão: {e}"
def _llm(prompt, system, log, etapa):
    ok, texto, prov, erros = get_llm_response(st.session_state.session_id, prompt, st.session_state.get("model_sel", "groq"), system=system, use_history=False)
    if ok: log.append("✅ " + etapa + ": " + str(prov)); return texto
    log.append("❌ " + etapa + ": " + texto[:150]); return ""
def run_pipeline(pedido, quer_img, quer_vid):
    log = []; contexto, fonte = web_search(pedido); log.append("🔎 Busca (" + fonte + "): ok" if contexto else "⚪ Busca: sem resultados")
    resumo = _llm("Pedido: " + pedido + "\n\nContexto:\n" + contexto[:4000], "Você é pesquisador. Resuma em português, até 8 linhas, fatos úteis. Não invente.", log, "Resumo") if contexto else ""
    sys_b = "Você é engenheiro de prompts. Responda EXATAMENTE em 2 linhas:\nIMAGEM: <prompt detalhado em inglês>\nVIDEO: <prompt em inglês, ação 5s>"
    bruto = _llm("Pedido: " + pedido + "\n\nFatos:\n" + resumo, sys_b, log, "Prompt otimizado")
    m_img = re.search(r"IMAGEM:\s*(.+)", bruto or ""); m_vid = re.search(r"VIDEO:\s*(.+)", bruto or "")
    prompt_img = m_img.group(1).strip() if m_img else pedido; prompt_vid = m_vid.group(1).strip() if m_vid else prompt_img
    img = make_image(prompt_img, log) if quer_img else None; vid = make_video(prompt_vid, log) if quer_vid else None
    return {"log": log, "resumo": resumo, "prompt_img": prompt_img, "prompt_vid": prompt_vid, "img": img, "vid": vid}
def processar_prompt(prompt):
    sid = st.session_state.session_id; low = prompt.lower()
    if low.startswith("aprenda") or low.startswith("aprende") or "ensinar" in low or low.startswith("zeus aprenda"):
        conteudo = re.sub(r"(?i)^(zeus\s+)?(aprenda|aprende|ensinar)\s*[:\-]?\s*", "", prompt).strip()
        if conteudo:
            if "|" in conteudo: chave, cont = [x.strip() for x in conteudo.split("|", 1)]
            else: chave, cont = conteudo[:40], conteudo
            ok = zeus_salvar_aprendizado(chave, cont, origem="ensinar")
            resp = f"✅ Aprendi! Salvei na hora no B2 como **{chave}**. Agora quando perguntar eu lembro do Cofre." if ok else "❌ Não consegui salvar no B2. Veja o diagnóstico."
            st.session_state.messages.append({"role": "user", "content": prompt}); add_message(sid, "user", prompt)
            st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(sid, "assistant", resp); return resp
    if "telegram" in low and ("manda" in low or "envia" in low or "enviar" in low):
        t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*", "", prompt).strip(" .,!?:-") or "Alerta Zeus."
        resp = zf_enviar_telegram(t)
        st.session_state.messages.append({"role": "user", "content": prompt}); add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(sid, "assistant", resp); return resp
    st.session_state.messages.append({"role": "user", "content": prompt}); add_message(sid, "user", prompt)
    resp = zeus_responder_texto(sid, prompt)
    st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(sid, "assistant", resp)
    st.session_state.last_info = "✅ Zeus respondeu (B2 primeiro, depois IA)"; return resp
def main():
    if "session_id" not in st.session_state: st.session_state.session_id = "zeus_web_1"
    if "messages" not in st.session_state: st.session_state.messages = []
    if "model_sel" not in st.session_state: st.session_state.model_sel = "groq"
    if "web_mode" not in st.session_state: st.session_state.web_mode = "Automática"
    if "tema" not in st.session_state: st.session_state.tema = "Cyberpunk"
    if "avatar" not in st.session_state: st.session_state.avatar = "⚡ Zeus Padrão"
    if "voz_sel" not in st.session_state: st.session_state.voz_sel = "Francisca (pt-BR ♀)"
    inject_theme(st.session_state.tema); init_db()
    vozes = listar_vozes_edge()
    with st.sidebar:
        st.markdown(f"<h2>{AVATARES.get(st.session_state.avatar, '⚡')} Zeus 3.0</h2>", unsafe_allow_html=True)
        st.session_state.avatar = st.selectbox("👤 Avatar do Zeus", list(AVATARES.keys()), index=list(AVATARES.keys()).index(st.session_state.avatar))
        st.session_state.tema = st.selectbox("🎨 Tema", list(THEMES.keys()), index=list(THEMES.keys()).index(st.session_state.tema))
        st.session_state.model_sel = st.selectbox("🧠 Modelo principal", PROVIDERS, index=PROVIDERS.index(st.session_state.model_sel) if st.session_state.model_sel in PROVIDERS else 0)
        st.session_state.web_mode = st.selectbox("🌐 Busca na web", ["Automática", "Sempre", "Desligada"], index=["Automática", "Sempre", "Desligada"].index(st.session_state.web_mode))
        st.text_input("Session ID", key="session_id")
        if st.button("🧹 Limpar conversa", use_container_width=True): st.session_state.messages = []; st.rerun()
        st.markdown("---")
        st.subheader("🔊 Voz do Zeus")
        st.session_state.voz_sel = st.selectbox("Voz", list(vozes.keys()), index=0 if st.session_state.voz_sel not in vozes else list(vozes.keys()).index(st.session_state.voz_sel))
        st.session_state.falar_auto = st.checkbox("Falar respostas automaticamente", value=False)
        st.markdown("---")
        st.subheader("🎵 【entity-Spotify¦canonical_name=Spotify】")
        musica = st.text_input("Buscar música no 【entity-Spotify¦canonical_name=Spotify】", key="spotify_q", placeholder="Ex.: Legião Urbana")
        if st.button("🔍 Buscar no 【entity-Spotify¦canonical_name=Spotify】", use_container_width=True) and musica: st.session_state.spotify_res = spotify_buscar(musica)
        if st.session_state.get("spotify_res"): st.markdown(st.session_state.spotify_res)
        st.markdown("---")
        mostrar_diagnostico_cofre()
        if st.button("📩 Testar Telegram", use_container_width=True): st.info(zf_enviar_telegram("✅ Teste Zeus: B2 conectado e Telegram respondendo."))
    avatar_emoji = AVATARES.get(st.session_state.avatar, "⚡")
    st.title(f"{avatar_emoji} Zeus 3.0")
    st.caption("B2 na hora + 03:00 GitHub->B2 · Ele busca primeiro no B2 · Telegram 24h mesmo com chat fechado")
    with st.expander("🧬 Caixa de Evolução - O que o Zeus já aprendeu (B2)", expanded=False):
        aprendidos = listar_aprendizados(30)
        if aprendidos:
            for chave, conteudo, ts in aprendidos: st.write(f"**{chave}** — {conteudo[:200]}  `({ts})`")
        else: st.caption("Nenhum aprendizado ainda. Ensina ele abaixo.")
        st.markdown("**Ensinar coisa nova:**")
        st.caption("Ex.: `aprenda: meu time | meu time é o Cruzeiro`")
        novo = st.text_input("O que ensinar?", key="ensinar_input", placeholder="aprenda: chave | conteúdo")
        if st.button("🧠 Ensinar Zeus e salvar no B2", use_container_width=True) and novo: processar_prompt(novo); st.rerun()
    with st.expander("📸 Tirar foto / Analisar foto / Editar foto", expanded=False):
        c1, c2 = st.columns(2)
        foto_cam = c1.camera_input("📷 Tirar foto agora")
        foto_up = c2.file_uploader("🖼️ Ou enviar foto", type=["png", "jpg", "jpeg", "webp"])
        foto = foto_cam or foto_up
        pergunta_foto = st.text_input("O que quer saber da foto?", key="pergunta_foto", placeholder="Ex.: o que tem nessa foto?")
        if foto and st.button("👁️ Analisar foto", use_container_width=True):
            b64 = base64.b64encode(foto.getvalue()).decode(); resp = analisar_imagem(b64, foto.type or "image/jpeg", pergunta_foto or "Descreva essa foto em português.")
            st.session_state.messages.append({"role": "assistant", "content": resp}); add_message(st.session_state.session_id, "assistant", resp); st.markdown(resp)
        edit_prompt = st.text_input("Editar essa foto - o que mudar?", key="edit_foto", placeholder="Ex.: deixa o fundo azul")
        if foto and edit_prompt and st.button("✏️ Editar foto", use_container_width=True):
            log = []; out, tipo = editar_imagem(foto.getvalue(), edit_prompt, pergunta_foto or "foto", log)
            if out: st.image(out, caption=f"Foto {tipo}")
            st.write("\n\n".join(log))
    with st.expander("🔗 Pipeline Multi-IA (busca → prompt otimizado → imagem/vídeo)", expanded=False):
        pedido = st.text_area("O que você quer criar?", key="pipe_req", placeholder="Ex.: dragão de cristal sobre cidade futurista")
        c1, c2 = st.columns(2)
        quer_img = c1.checkbox("🖼️ Gerar imagem", value=True, key="pipe_img"); quer_vid = c2.checkbox("🎬 Gerar vídeo (experimental)", value=False, key="pipe_vid")
        if st.button("🚀 Executar pipeline", key="pipe_run") and pedido.strip():
            with st.spinner("Encadeando IAs..."): st.session_state.pipe_res = run_pipeline(pedido.strip(), quer_img, quer_vid)
        res = st.session_state.get("pipe_res")
        if res:
            if res["resumo"]: st.markdown("**Resumo da pesquisa**"); st.write(res["resumo"])
            st.markdown("**Prompt otimizado (imagem)**"); st.code(res["prompt_img"], language="text")
            if res["img"]: st.image(res["img"], caption="Imagem gerada")
            if res["vid"]: st.video(res["vid"])
            with st.expander("Log do pipeline", expanded=not (res["img"] or res["vid"])): st.write("\n\n".join(res["log"]))
    for m in st.session_state.messages:
        avatar_msg = AVATARES.get(st.session_state.avatar, "⚡") if m["role"] == "assistant" else "🧑"
        with st.chat_message(m["role"], avatar=avatar_msg): st.markdown(m["content"])
    if st.session_state.get("last_info"): st.caption(st.session_state["last_info"])
    audio = st.audio_input("🎙️ Falar com Zeus")
    if audio is not None:
        texto, erro = transcrever_audio(audio.getvalue(), audio.type)
        if texto: processar_prompt(texto); st.rerun()
        elif erro: st.warning(erro)
    prompt = st.chat_input("Fala com o Zeus...")
    if prompt: processar_prompt(prompt); st.rerun()
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        col1, col2 = st.columns(2)
        if col1.button("🔊 Ouvir última resposta"):
            a = sintetizar(st.session_state.messages[-1]["content"], vozes.get(st.session_state.voz_sel, "pt-BR-FranciscaNeural"))
            if a: st.audio(a, format="audio/mp3")
        if col2.button("📩 Mandar última resposta no Telegram"): st.info(zf_enviar_telegram(st.session_state.messages[-1]["content"][:3500]))
if __name__ == "__main__": main()
