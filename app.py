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
import unicodedata
import zipfile
from datetime import datetime, timezone
from datetime import datetime as _dtm, timezone as _tzn
from difflib import SequenceMatcher as _SM
from email.message import EmailMessage as _EmailMessage
from email.utils import parseaddr as _parseaddr

import requests
import csv as _csv
import html as _html
import smtplib as _smtplib
import ssl as _ssl
import unicodedata as _u
import streamlit as st
import streamlit.components.v1 as components

try:
    from openai import OpenAI  # cliente OpenAI (usado para DeepInfra)
except ImportError:
    OpenAI = None

# Importação dinâmica das habilidades geradas pelo Zeus e do gerenciador do GitHub
try:
    import zeus_skills
except Exception:
    pass

try:
    from github_manager import salvar_no_github
except Exception:
    salvar_no_github = None

try:
    from cofre_b2 import salvar_texto, ler_texto, salvar_json, ler_json
except Exception:
    salvar_texto = None
    ler_texto = None
    salvar_json = None
    ler_json = None

# Módulos principais do Zeus (ações, roteamento/memória/web, agente e laboratório)
import zeus_acoes
processar_prompt = zeus_acoes.instalar(globals())

import zeus_roteador
zeus_roteador.instalar(globals())

try:
    import zeus_agente
    processar_prompt = zeus_agente.instalar(globals())
except Exception:
    pass

try:
    import zeus_lab
    processar_prompt = zeus_lab.instalar(globals())
except Exception:
    pass
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
# CHAVES + BANCO DE DADOS + COFRE BACKBLAZE B2  (bloco único)
# Substitui AS DUAS seções antigas ("BANCO DE DADOS + COFRE" e "CHAVES").
# Requer no requirements.txt:  boto3
# Opcional (fallback direto B2): b2sdk
# ============================================================
import contextlib
import io
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time

import streamlit as st

_log = logging.getLogger("zeus.cofre")

# ------------------------------------------------------------
# CHAVES (definidas ANTES do banco, para nunca dar NameError silencioso)
# ------------------------------------------------------------
CHAVES_PADRAO = {  # Nunca coloque chaves reais aqui. Use .streamlit/secrets.toml
}


def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", v).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v


def _secret(key: str, default: str = "") -> str:
    """Procura no topo dos secrets, depois dentro de [seções], depois nas variáveis de ambiente."""
    try:
        sec = st.secrets
        val = sec.get(key)
        if not val:
            for _, bloco in sec.items():
                if hasattr(bloco, "get"):
                    val = bloco.get(key)
                    if val:
                        break
        if val:
            return _limpar_chave(str(val))
    except Exception:
        pass
    return _limpar_chave(os.environ.get(key) or default or CHAVES_PADRAO.get(key, ""))


# ------------------------------------------------------------
# CONFIGURAÇÃO DO COFRE
# ------------------------------------------------------------
DB_FILE = "zeus_chat.db"
COFRE_DB_KEY = "banco/zeus_chat.db"
COFRE_INTERVALO_SEG = 5     # no máximo 1 envio a cada 5 s (o resto fica pendente)
COFRE_BACKOFF_SEG = 30      # após uma falha, espera antes de tentar de novo


@st.cache_resource
def _cofre_ctx():
    """Estado compartilhado do processo (sobrevive aos reruns do Streamlit)."""
    return {
        "lock": threading.RLock(),
        "nuvem_ok": False,
        "inicializado": False,
        "pendente": False,
        "ultimo_envio": 0.0,
        "bloqueado_ate": 0.0,
        "erro": "",
        "ultimo_tamanho": 0,
        "resultado_download": "",
        "modo_ativo": "s3",
    }


@st.cache_resource
def _criar_cliente_s3(endpoint: str, key_id: str, app_key: str):
    import boto3
    from botocore.config import Config

    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name=m.group(1) if m else None,
        config=Config(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _criar_cliente_b2_nativo(key_id: str, app_key: str):
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", key_id, app_key)
    return api


def _obter_config_cofre():
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    bucket = _secret("B2_BUCKET", "zeus-cofre-danilo")
    key_id, app_key = _secret("B2_KEY_ID"), _secret("B2_APP_KEY")
    return endpoint, bucket, key_id, app_key


def _obter_cliente_cofre():
    """Conecta ao bucket Backblaze B2 usando _secret() ou conectar_cofre()."""
    endpoint, bucket, key_id, app_key = _obter_config_cofre()

    if key_id and app_key:
        return _criar_cliente_s3(endpoint, key_id, app_key), bucket

    if "conectar_cofre" in globals() and callable(globals()["conectar_cofre"]):
        return globals()["conectar_cofre"]()

    return None, bucket


def _eh_nao_encontrado(e: Exception) -> bool:
    resp = getattr(e, "response", None) or {}
    codigo = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if codigo == "NoSuchBucket":
        return False
    return codigo in ("NoSuchKey", "404", "NotFound") or status == 404


def _detalhe_erro(e: Exception) -> str:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error") or {}
    codigo = err.get("Code", "")
    msg = err.get("Message", "")
    base = f"{type(e)._name_}: {e}"
    if codigo or msg:
        return f"{base} [{codigo} - {msg}]"
    return base


def _dica(erro: str) -> str:
    dicas = {
        "InvalidAccessKeyId": "B2_KEY_ID errado (use o keyID, não o nome da chave).",
        "SignatureDoesNotMatch": "B2_APP_KEY errada, ou endpoint/região não é o do seu bucket.",
        "AccessDenied": "a chave não tem permissão neste bucket (precisa ler e escrever).",
        "NoSuchBucket": "nome do bucket errado, ou endpoint de outra região.",
        "ImportError": "falta o pacote boto3 no requirements.txt.",
        "ModuleNotFoundError": "falta o pacote boto3 no requirements.txt.",
        "EndpointConnectionError": "endpoint inválido ou sem acesso à internet.",
        "InvalidAccessKey": "chave inválida ou expirada; crie uma nova no B2.",
    }
    for k, v in dicas.items():
        if k in erro:
            return v
    return ""


# ------------------------------------------------------------
# BANCO LOCAL
# ------------------------------------------------------------
def _conectar():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
        "role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sessao ON messages(session_id, id)")
    conn.commit()
    return conn


def contar_mensagens() -> int:
    with contextlib.closing(_conectar()) as conn:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def _snapshot_local() -> bytes:
    """Cópia consistente do banco (API de backup do SQLite), mesmo com escrita em andamento."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src, dest = _conectar(), sqlite3.connect(tmp)
    try:
        src.backup(dest)
        dest.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        src.close()
        with contextlib.suppress(Exception):
            dest.close()
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _mesclar_banco(corpo: bytes):
    """Junta as mensagens da nuvem no banco local. Nunca apaga nada, nunca duplica."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(corpo)
        conn = _conectar()
        try:
            conn.execute("ATTACH DATABASE ? AS nuvem", (tmp,))
            tem = conn.execute(
                "SELECT 1 FROM nuvem.sqlite_master WHERE type='table' AND name='messages'"
            ).fetchone()
            if tem:
                conn.execute(
                    """
                    INSERT INTO main.messages (session_id, role, content, timestamp)
                    SELECT n.session_id, n.role, n.content, n.timestamp FROM nuvem.messages n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM main.messages m
                        WHERE m.session_id = n.session_id AND m.role = n.role
                          AND m.content = n.content AND m.timestamp IS n.timestamp)
                    ORDER BY n.id
                    """
                )
            conn.commit()
            conn.execute("DETACH DATABASE nuvem")
        finally:
            conn.close()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _baixar_corpo_s3(cliente, bucket: str) -> bytes:
    return cliente.get_object(Bucket=bucket, Key=COFRE_DB_KEY)["Body"].read()


def _enviar_corpo_s3(cliente, bucket: str, corpo: bytes):
    cliente.put_object(
        Bucket=bucket,
        Key=COFRE_DB_KEY,
        Body=corpo,
        ContentType="application/octet-stream",
    )


def _baixar_corpo_b2_nativo(key_id: str, app_key: str, bucket_nome: str) -> bytes:
    api = _criar_cliente_b2_nativo(key_id, app_key)
    bucket = api.get_bucket_by_name(bucket_nome)
    buf = io.BytesIO()
    try:
        bucket.download_file_by_name(COFRE_DB_KEY).save(buf)
        return buf.getvalue()
    except Exception as e:
        if "file_not_found" in str(e).lower() or "404" in str(e):
            return b""
        raise


def _enviar_corpo_b2_nativo(key_id: str, app_key: str, bucket_nome: str, corpo: bytes):
    api = _criar_cliente_b2_nativo(key_id, app_key)
    bucket = api.get_bucket_by_name(bucket_nome)
    bucket.upload_bytes(corpo, COFRE_DB_KEY)


def _processar_bytes_nuvem(corpo: bytes) -> str:
    ctx = _cofre_ctx()
    if not corpo:
        ctx["nuvem_ok"], ctx["resultado_download"] = True, "vazio"
        return "vazio"
    if not corpo.startswith(b"SQLite format 3\x00"):
        raise ValueError("o objeto na nuvem não é um banco SQLite; não vou sobrescrevê-lo")
    _mesclar_banco(corpo)
    ctx["nuvem_ok"], ctx["resultado_download"] = True, "mesclado"
    return "mesclado"


def _baixar_e_mesclar(cliente, bucket: str) -> str:
    """Retorna 'mesclado' | 'ausente' | 'vazio'. Levanta exceção em qualquer falha REAL."""
    ctx = _cofre_ctx()
    _, _, key_id, app_key = _obter_config_cofre()
    try:
        corpo = _baixar_corpo_s3(cliente, bucket)
        ctx["modo_ativo"] = "s3"
        return _processar_bytes_nuvem(corpo)
    except Exception as e_s3:
        if _eh_nao_encontrado(e_s3):
            ctx["nuvem_ok"], ctx["resultado_download"], ctx["modo_ativo"] = True, "ausente", "s3"
            return "ausente"
        if key_id and app_key:
            try:
                corpo = _baixar_corpo_b2_nativo(key_id, app_key, bucket)
                ctx["modo_ativo"] = "b2sdk"
                if not corpo:
                    ctx["nuvem_ok"], ctx["resultado_download"] = True, "ausente"
                    return "ausente"
                return _processar_bytes_nuvem(corpo)
            except Exception:
                pass
        raise RuntimeError(_detalhe_erro(e_s3)) from e_s3


def _enviar_para_nuvem(cliente, bucket: str, corpo: bytes):
    ctx = _cofre_ctx()
    _, _, key_id, app_key = _obter_config_cofre()
    if ctx.get("modo_ativo") == "b2sdk" and key_id and app_key:
        _enviar_corpo_b2_nativo(key_id, app_key, bucket, corpo)
        return
    try:
        _enviar_corpo_s3(cliente, bucket, corpo)
        ctx["modo_ativo"] = "s3"
    except Exception as e_s3:
        if key_id and app_key:
            try:
                _enviar_corpo_b2_nativo(key_id, app_key, bucket, corpo)
                ctx["modo_ativo"] = "b2sdk"
                return
            except Exception:
                pass
        raise RuntimeError(_detalhe_erro(e_s3)) from e_s3


# ------------------------------------------------------------
# COFRE B2
# ------------------------------------------------------------
def baixar_db_cofre() -> bool:
    """Traz a nuvem para o banco local (mescla). Retorna True se a nuvem foi lida com sucesso."""
    ctx = _cofre_ctx()
    with ctx["lock"]:
        if ctx["nuvem_ok"]:
            return True
        try:
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket:
                ctx["erro"] = "Credenciais do B2 não encontradas (B2_KEY_ID / B2_APP_KEY)."
                return False
            _baixar_e_mesclar(cliente, bucket)
            ctx["erro"] = ""
            return True
        except Exception as e:
            ctx["erro"] = _detalhe_erro(e)
            _log.warning("Cofre B2 (download): %s", ctx["erro"])
            return False


def sincronizar_db_cofre(forcar: bool = False) -> bool:
    """
    Mescla o que há na nuvem e envia o banco local. Se o download falhar por qualquer motivo
    real, NADA é enviado (isso evita sobrescrever o cofre com um banco vazio).
    """
    ctx = _cofre_ctx()
    with ctx["lock"]:
        agora = time.time()
        if not forcar:
            if agora < ctx["bloqueado_ate"]:
                ctx["pendente"] = True
                return False
            if agora - ctx["ultimo_envio"] < COFRE_INTERVALO_SEG:
                ctx["pendente"] = True
                return False
        try:
            _conectar().close()
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket:
                ctx["erro"] = "Credenciais do B2 não encontradas (B2_KEY_ID / B2_APP_KEY)."
                ctx["bloqueado_ate"] = agora + COFRE_BACKOFF_SEG
                return False
            _baixar_e_mesclar(cliente, bucket)
            corpo = _snapshot_local()
            _enviar_para_nuvem(cliente, bucket, corpo)
            ctx.update(
                ultimo_envio=time.time(),
                pendente=False,
                erro="",
                ultimo_tamanho=len(corpo),
                bloqueado_ate=0.0,
            )
            return True
        except Exception as e:
            ctx["erro"] = _detalhe_erro(e)
            ctx["bloqueado_ate"] = time.time() + COFRE_BACKOFF_SEG
            ctx["pendente"] = True
            _log.warning("Cofre B2 (envio): %s", ctx["erro"])
            return False


# ------------------------------------------------------------
# API USADA PELO RESTO DO APP (mesmos nomes de antes)
# ------------------------------------------------------------
def init_db():
    try:
        _conectar().close()
        ctx = _cofre_ctx()
        if not ctx["inicializado"]:
            if sincronizar_db_cofre():
                ctx["inicializado"] = True
        elif ctx["pendente"]:
            sincronizar_db_cofre()
    except Exception as e:
        _log.warning("init_db: %s", e)


def add_message(session_id, role, content):
    try:
        with contextlib.closing(_conectar()) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            conn.commit()
    except Exception as e:
        _log.warning("add_message: %s", e)
        return
    sincronizar_db_cofre()


def get_recent_context(session_id, limit=6):
    try:
        if not _cofre_ctx()["nuvem_ok"]:
            baixar_db_cofre()
        with contextlib.closing(_conectar()) as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []


# ------------------------------------------------------------
# DIAGNÓSTICO (chame mostrar_diagnostico_cofre() UMA vez, em qualquer ponto do app)
# ------------------------------------------------------------
def diagnostico_cofre():
    """Lista de (verificação, ok, detalhe). Nunca expõe a chave secreta."""
    itens = []

    def add(nome, ok, det=""):
        itens.append((nome, bool(ok), det))

    try:
        import boto3

        add("Pacote boto3", True, boto3._version_)
    except ImportError:
        add("Pacote boto3", False, "não instalado: adicione 'boto3' ao requirements.txt")

    try:
        import b2sdk

        add("Pacote b2sdk", True, getattr(b2sdk, "_version_", "instalado"))
    except ImportError:
        add("Pacote b2sdk", False, "opcional: só usado se o endpoint S3 falhar")

    endpoint, bucket, key_id, app_key = _obter_config_cofre()
    add(
        "B2_KEY_ID",
        key_id,
        f"{key_id[:6]}… ({len(key_id)} caracteres)" if key_id else "não encontrado nos secrets",
    )
    add(
        "B2_APP_KEY",
        app_key,
        f"{len(app_key)} caracteres" if app_key else "não encontrado nos secrets",
    )
    add("B2_ENDPOINT", endpoint, endpoint)
    add("B2_BUCKET", bucket, bucket)
    add("Modo ativo", True, _cofre_ctx().get("modo_ativo", "s3"))

    try:
        cliente, _ = _obter_cliente_cofre()
        if cliente:
            cliente.head_bucket(Bucket=bucket)
            add("Acesso ao bucket (S3)", True, "ok")
            try:
                tam = cliente.head_object(Bucket=bucket, Key=COFRE_DB_KEY).get("ContentLength", 0)
                add(
                    f"Arquivo {COFRE_DB_KEY} na nuvem",
                    tam > 0,
                    f"{tam} bytes" if tam else "existe mas tem 0 bytes",
                )
            except Exception as e:
                add(
                    f"Arquivo {COFRE_DB_KEY} na nuvem",
                    False,
                    "ainda não existe" if _eh_nao_encontrado(e) else _detalhe_erro(e),
                )
    except Exception as e:
        det_erro = _detalhe_erro(e)
        add("Acesso ao bucket (S3)", False, f"{det_erro} — {_dica(det_erro)}")

    add(
        "Banco local",
        os.path.exists(DB_FILE),
        f"{os.path.getsize(DB_FILE)} bytes · {contar_mensagens()} mensagens"
        if os.path.exists(DB_FILE)
        else "ainda não criado",
    )
    ctx = _cofre_ctx()
    if ctx["erro"]:
        add("Último erro", False, f"{ctx['erro']} — {_dica(ctx['erro'])}")
    return itens


def mostrar_diagnostico_cofre():
    with st.expander("🔧 Diagnóstico do Cofre B2", expanded=False):
        if st.button("Testar envio agora", key="cofre_diag_btn", use_container_width=True):
            ok = sincronizar_db_cofre(forcar=True)
            (st.success if ok else st.error)(
                f"Envio concluído ({_cofre_ctx()['ultimo_tamanho']} bytes)."
                if ok
                else "Envio falhou, veja abaixo."
            )
            for nome, ok_, det in diagnostico_cofre():
                st.write(f"{'✅' if ok_ else '❌'} *{nome}* — {det}")
        else:
            st.caption("Toque no botão para testar a conexão e ver o motivo exato de qualquer falha.")
# ------------------------------------------------------------
# COMANDOS DIRETOS NO CHAT PARA TESTAR O COFRE B2
# ------------------------------------------------------------
def salvar_arquivo_cofre(nome_arquivo: str, conteudo: str) -> tuple[bool, str]:
    nome_limpo = re.sub(r"[^a-zA-Z0-9./-]", "", (nome_arquivo or "").strip()).lstrip("/")
    if not nome_limpo:
        nome_limpo = "cofre/teste_chat.txt"
    elif "/" not in nome_limpo:
        nome_limpo = f"cofre/{nome_limpo}"

    dados = (conteudo or "").encode("utf-8")
    cliente, bucket = _obter_cliente_cofre()
    ctx = _cofre_ctx()
    _, _, key_id, app_key = _obter_config_cofre()

    if not bucket:
        return False, "Bucket do B2 não configurado."

    try:
        if cliente and ctx.get("modo_ativo") != "b2sdk":
            cliente.put_object(
                Bucket=bucket,
                Key=nome_limpo,
                Body=dados,
                ContentType="text/plain; charset=utf-8",
            )
            ctx["modo_ativo"] = "s3"
            return True, f"Arquivo salvo no B2 ({nome_limpo}, {len(dados)} bytes) via S3."
    except Exception as e_s3:
        if not (key_id and app_key):
            return False, f"Falha ao salvar no B2: {_detalhe_erro(e_s3)}"

    try:
        if not (key_id and app_key):
            return False, "Credenciais B2_KEY_ID / B2_APP_KEY ausentes."
        api = _criar_cliente_b2_nativo(key_id, app_key)
        b2_bucket = api.get_bucket_by_name(bucket)
        b2_bucket.upload_bytes(dados, nome_limpo)
        ctx["modo_ativo"] = "b2sdk"
        return True, f"Arquivo salvo no B2 ({nome_limpo}, {len(dados)} bytes) via b2sdk."
    except Exception as e_b2:
        return False, f"Falha ao salvar no B2: {_detalhe_erro(e_b2)}"


def apagar_arquivo_cofre(nome_arquivo: str) -> tuple[bool, str]:
    nome_limpo = (nome_arquivo or "").strip().lstrip("/")
    if not nome_limpo:
        return False, "Informe o nome do arquivo para apagar."

    candidatos = [nome_limpo]
    if "/" not in nome_limpo:
        candidatos.append(f"cofre/{nome_limpo}")

    cliente, bucket = _obter_cliente_cofre()
    ctx = _cofre_ctx()
    _, _, key_id, app_key = _obter_config_cofre()

    if not bucket:
        return False, "Bucket do B2 não configurado."

    for chave in candidatos:
        try:
            if cliente and ctx.get("modo_ativo") != "b2sdk":
                cliente.delete_object(Bucket=bucket, Key=chave)
                return True, f"Arquivo apagado do B2 ({chave})."
        except Exception:
            pass

        try:
            if key_id and app_key:
                api = _criar_cliente_b2_nativo(key_id, app_key)
                b2_bucket = api.get_bucket_by_name(bucket)
                versao = b2_bucket.get_file_info_by_name(chave)
                b2_bucket.delete_file_version(versao.id_, chave)
                return True, f"Arquivo apagado do B2 ({chave})."
        except Exception:
            pass

    return False, f"Não foi possível apagar {nome_limpo} (verifique se o nome está exato no bucket)."


def listar_arquivos_cofre(limite: int = 20) -> tuple[bool, str]:
    cliente, bucket = _obter_cliente_cofre()
    _, _, key_id, app_key = _obter_config_cofre()
    if not bucket:
        return False, "Bucket do B2 não configurado."

    try:
        if cliente:
            resp = cliente.list_objects_v2(Bucket=bucket, MaxKeys=limite)
            itens = [obj["Key"] for obj in resp.get("Contents", [])]
            if not itens:
                return True, "Bucket vazio no momento (0 arquivos)."
            return True, "Arquivos no B2:\n- " + "\n- ".join(itens)
    except Exception:
        pass

    try:
        if key_id and app_key:
            api = _criar_cliente_b2_nativo(key_id, app_key)
            b2_bucket = api.get_bucket_by_name(bucket)
            itens = [fv.file_name for fv, _ in b2_bucket.ls(latest_only=True, show_versions=False)]
            itens = itens[:limite]
            if not itens:
                return True, "Bucket vazio no momento (0 arquivos)."
            return True, "Arquivos no B2:\n- " + "\n- ".join(itens)
    except Exception as e:
        return False, f"Falha ao listar arquivos do B2: {_detalhe_erro(e)}"

    return False, "Sem credenciais válidas para listar o B2."


def tratar_comando_cofre_chat(mensagem: str) -> str | None:
    """
    Se a mensagem começar com b2_salvar:, b2_apagar: ou b2_listar, executa direto no B2
    e devolve a resposta pronta para o chat. Retorna None se não for comando de cofre.
    """
    txt = (mensagem or "").strip()
    baixo = txt.lower()

    if baixo == "b2_listar" or baixo.startswith("b2_listar"):
        _, msg = listar_arquivos_cofre()
        return msg

    if baixo.startswith("b2_salvar:"):
        corpo_cmd = txt[len("b2_salvar:"):].strip()
        if "|" in corpo_cmd:
            nome, conteudo = corpo_cmd.split("|", 1)
        else:
            nome, conteudo = "cofre/teste_chat.txt", corpo_cmd
        _, msg = salvar_arquivo_cofre(nome.strip(), conteudo.strip())
        return msg

    if baixo.startswith("b2_apagar:"):
        nome = txt[len("b2_apagar:"):].strip()
        _, msg = apagar_arquivo_cofre(nome)
        return msg

    return None

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
PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter", "mistral", "cloudflare"]
KEYS = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "github": "GITHUB_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
    "cloudflare_account": "CLOUDFLARE_ACCOUNT_ID",
}
MODEL_SECRET = {
    "groq": "GROQ_MODEL",
    "gemini": "GEMINI_MODEL",
    "claude": "ANTHROPIC_MODEL",
    "huggingface": "HF_MODEL",
    "github": "GITHUB_MODEL",
    "deepinfra": "DEEPINFRA_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "mistral": "MISTRAL_MODEL",
    "cloudflare": "CLOUDFLARE_MODEL",
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
    "mistral": ["mistral-small-latest", "open-mistral-nemo"],
    "cloudflare": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-8b-instruct"],
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
            try:
                data = r.json()
            except Exception:
                return 200, (r.text or "").strip()
            return 200, (data["choices"][0]["message"].get("content") or "").strip()
            return r.status_code, r.text


def _call_huggingface(api_key, model, msgs, system=None):
    chave = (
        (api_key or "").strip()
        or (_get_secret("HF_TOKEN") if "_get_secret" in globals() else "")
        or (_get_secret("HUNGER_FACE_API_KEY") if "_get_secret" in globals() else "")
        or (_get_secret("HUGGINGFACE_API_KEY") if "_get_secret" in globals() else "")
        or os.getenv("HF_TOKEN", "")
        or os.getenv("HUNGER_FACE_API_KEY", "")
    ).strip()
    if not chave:
        return False, "chave do Hugging Face não encontrada nos Secrets."

    modelo_limpo = (model or "").strip()
    if not modelo_limpo or modelo_limpo.lower() in ("default", "auto", "none"):
        modelo_limpo = "Qwen/Qwen2.5-7B-Instruct"

    try:
        res = _call_openai_compat(
            "https://router.huggingface.co/v1/chat/completions",
            chave,
            modelo_limpo,
            msgs,
            system,
        )
        if isinstance(res, tuple) and len(res) == 2:
            ok, info = res
            if ok and info not in (None, ""):
                return True, info
            if info not in (None, ""):
                return False, str(info)
        elif res not in (None, ""):
            return True, str(res)
    except Exception:
        pass

    try:
        import requests

        mensagens = []
        if system:
            mensagens.append({"role": "system", "content": system})
        for m in (msgs or []):
            if isinstance(m, dict) and "role" in m and "content" in m:
                mensagens.append({"role": m["role"], "content": m["content"]})
        if not mensagens:
            mensagens = [{"role": "user", "content": "Responda apenas: ok"}]

        resp = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {chave}",
                "Content-Type": "application/json",
            },
            json={
                "model": modelo_limpo,
                "messages": mensagens,
                "max_tokens": 64,
            },
            timeout=25,
        )
        if resp.status_code == 200:
            dados = resp.json()
            escolhas = dados.get("choices") or []
            if escolhas:
                texto = ((escolhas[0].get("message") or {}).get("content") or "").strip()
                if texto:
                    return True, texto
            return False, f"HTTP 200 sem conteúdo de texto: {resp.text[:200]}"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, f"falha na conexão com Hugging Face: {e}"


def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat(
        "https://models.github.ai/inference/chat/completions",
        api_key, model, msgs, system,
        {"Accept": "application/json"},
    )


def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat(
        "https://models.github.ai/inference/chat/completions",
        api_key, model, msgs, system,
        {"Accept": "application/json"},
    )


def _get_secret(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, "")


def _call_mistral(api_key, model, msgs, system=None):
    key = (api_key or _secret("MISTRAL_API_KEY") or "").strip()
    if not key:
        return False, "chave MISTRAL_API_KEY ausente"

    payload_msgs = []
    if system:
        payload_msgs.append({"role": "system", "content": system})
    payload_msgs.extend(msgs)

    try:
        resp = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or "mistral-small-latest",
                "messages": payload_msgs,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code != 200:
            msg = data.get("message") or data.get("error") or resp.text
            return False, f"HTTP {resp.status_code}: {msg}"

        texto = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not texto:
            return False, "API retornou conteúdo vazio"
        return True, texto
    except Exception as e:
        return False, f"falha na requisição: {e}"


def _call_cloudflare(api_key, model, msgs, system=None, account_id=None):
    key = api_key or _get_secret("CLOUDFLARE_API_KEY")
    acc = account_id or _get_secret("CLOUDFLARE_ACCOUNT_ID")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/v1/chat/completions"
    return _call_openai_compat(url, key, model, msgs, system)

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
    key = (api_key or _get_secret("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return False, "chave OPENROUTER_API_KEY ausente"

    payload_msgs = []
    if system:
        payload_msgs.append({"role": "system", "content": system})
    payload_msgs.extend(msgs)

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://streamlit.io",
                "X-Title": "Zeus 3.0",
            },
            json={
                "model": model or "openrouter/free",
                "messages": payload_msgs,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("error")
            msg = err.get("message") if isinstance(err, dict) else (err or data.get("message") or resp.text)
            return False, f"HTTP {resp.status_code}: {msg}"

        texto = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not texto:
            return False, "API retornou conteúdo vazio"
        return True, texto
    except Exception as e:
        return False, f"falha na requisição: {e}"


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
    "mistral": _call_mistral, "cloudflare": _call_cloudflare,
}


def _resumo(text):
    low = text.lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return text[:120]


def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    resposta_cofre = tratar_comando_cofre_chat(prompt)
    if resposta_cofre is not None:
        return True, resposta_cofre, "cofre_b2", []

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
# ZEUS TELEGRAM REAL (Envio + Leitura e Resposta via Zeus)
# ============================================================
def _obter_credenciais_telegram() -> tuple[str, str]:
    token = ""
    chat_id = ""
    try:
        token = st.secrets.get("TELEGRAM_BOT_TOKEN") or st.secrets.get("telegram", {}).get("token", "")
        chat_id = st.secrets.get("TELEGRAM_CHAT_ID") or st.secrets.get("telegram", {}).get("chat_id", "")
    except Exception:
        pass
    token = str(token or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    return token, chat_id


def zf_enviar_telegram(texto: str, chat_id_destino: str = "") -> str:
    token, chat_id_padrao = _obter_credenciais_telegram()
    destino = str(chat_id_destino or chat_id_padrao or "").strip()

    if not token or not destino:
        return "❌ ERRO CRÍTICO: Token do Telegram ou Chat ID não encontrados no secrets.toml."

    msg_limpa = (texto or "").strip() or "Teste de telemetria e protocolo de bunker acionado por Danilo."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": destino,
        "text": f"🚨 [ZEUS - PROTOCOLO DE BUNKER] 🚨\n\nCriador: Danilo\n\n{msg_limpa[:3800]}",
        "parse_mode": "Markdown",
    }

    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200 and r.json().get("ok"):
            return "✅ SUCESSO REAL: Mensagem disparada diretamente nos servidores do Telegram para o Danilo!"
        return f"❌ FALHA NA API DO TELEGRAM (Status {r.status_code}): {r.text}"
    except Exception as e:
        return f"❌ EXCEÇÃO DE REDE NO TELEGRAM: {str(e)}"

def _rt_comando_telegram(texto: str) -> str:
    t = re.sub(
        r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*",
        "",
        texto,
    ).strip(" .,!?:-")
    if not t:
        t = "Alerta de telemetria e bunker disparado pelo criador Danilo."
    return zf_enviar_telegram(t)


def interceptar_comando_autonomo_telegram(texto: str) -> tuple[bool, str]:
    low = (texto or "").lower()
    if any(k in low for k in ("telegram", "tg", "bunker", "telemetria")):
        return True, _rt_comando_telegram(texto)
    return False, ""


def sincronizar_e_responder_telegram(responder_fn=None) -> int:
    """Lê mensagens novas enviadas ao bot no Telegram e responde usando o Zeus."""
    token, chat_id_autorizado = _obter_credenciais_telegram()
    if not token:
        return 0

    if "tg_last_update_id" not in st.session_state:
        st.session_state.tg_last_update_id = 0

    offset = int(st.session_state.tg_last_update_id) + 1
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 2, "allowed_updates": '["message"]'}, timeout=15)
        data = r.json() if r.status_code == 200 else {}
    except Exception:
        return 0

    if not data.get("ok"):
        return 0

    respondidas = 0
    for upd in data.get("result", []):
        uid = int(upd.get("update_id", 0))
        if uid >= int(st.session_state.tg_last_update_id):
            st.session_state.tg_last_update_id = uid

        msg = upd.get("message") or {}
        texto = (msg.get("text") or "").strip()
        cid = str((msg.get("chat") or {}).get("id", "")).strip()
        if not texto or not cid:
            continue
        if chat_id_autorizado and cid != chat_id_autorizado:
            continue

        if texto == "/start":
            resposta_zeus = "⚡ Zeus ativo no Telegram. Mande sua mensagem ou dúvida que eu respondo por aqui."
        elif callable(responder_fn):
            resposta_zeus = str(responder_fn(texto) or "Sem resposta no momento.")
        else:
            resposta_zeus = f"Recebido: {texto}"

        zf_enviar_telegram(resposta_zeus)
        respondidas += 1

    return respondidas
  # === FALLBACK OBRIGATÓRIO: Google -> DuckDuckGo -> Wikipédia (sem IA ou B2) ===
    if not ok:
        ctx_web, fonte = "", ""
        q = urllib.parse.quote_plus(prompt[:200])
        hdrs = {"User-Agent": "Mozilla/5.0"}

        # 1. Google
        try:
            g_url = f"https://www.google.com/search?q={q}&hl=pt-BR&gl=BR&num=5"
            with urllib.request.urlopen(urllib.request.Request(g_url, headers=hdrs), timeout=7) as gr:
                g_html = gr.read().decode("utf-8", errors="ignore")
            g_trechos = re.findall(r'<div class="BNeawe[^"]">(.?)</div>', g_html, re.S)
            g_limpos = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in g_trechos if len(re.sub(r"<[^>]+>", "", t).strip()) > 25]
            if g_limpos:
                ctx_web = "\n".join(f"- {x}" for x in g_limpos[:5])
                fonte = "Google Web"
        except Exception:
            ctx_web, fonte = "", ""

        # 2. DuckDuckGo HTML
        if not ctx_web.strip():
            try:
                d_url = f"https://html.duckduckgo.com/html/?q={q}"
                with urllib.request.urlopen(urllib.request.Request(d_url, headers=hdrs), timeout=7) as dr:
                    d_html = dr.read().decode("utf-8", errors="ignore")
                d_trechos = re.findall(r'class="result__snippet[^"]"[^>]>(.*?)</(?:a|div)>', d_html, re.S)
                d_limpos = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in d_trechos if t.strip()]
                if d_limpos:
                    ctx_web = "\n".join(f"- {x}" for x in d_limpos[:5])
                    fonte = "DuckDuckGo Web"
            except Exception:
                ctx_web, fonte = "", ""

        # 3. Wikipédia
        if not ctx_web.strip():
            try:
                w_url = f"https://pt.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&format=json&utf8=1"
                with urllib.request.urlopen(urllib.request.Request(w_url, headers=hdrs), timeout=7) as wr:
                    w_data = json.loads(wr.read().decode("utf-8", errors="ignore"))
                hits = w_data.get("query", {}).get("search", [])
                if hits:
                    ctx_web = "\n".join(
                        f"- {html.unescape(h.get('title',''))}: {html.unescape(re.sub(r'<[^>]+>', '', h.get('snippet','')))}"
                        for h in hits[:4]
                    )
                    fonte = "Wikipédia"
            except Exception as e_web:
                ctx_web = f"[erro busca web: {type(e_web)._name_}: {e_web}]"
                fonte = "Diagnóstico"
        if ctx_web and ctx_web.strip():
            response = (
                "🌐 As IAs não responderam. Busquei na internet por conta própria:\n\n"
                + ctx_web[:3800]
                + f"\n\n(Fonte: {fonte})"
            )
            ok = True
            prov = "Web Search (autônomo)"
            fonte_web = fonte
            erros = (erros or []) + ["fallback web forçado"]
        else:
            ctx2 = _search_ddg("explicação " + prompt[:200], n=4)
            if ctx2 and ctx2.strip():
                response = (
                    "🌐 Busca na web (alternativa):\n\n"
                    + ctx2[:3500]
                    + "\n\n(Fonte: Web aberta)"
                )
                ok = True
                prov = "Web Search (alternativa)"
                fonte_web = "Web aberta"
            else:
                response = (
                    "Não consegui contatar as IAs nem obter resultado útil na web neste momento. "
                    "Tente reformular a pergunta ou verifique as chaves de API / conexão."
                )
                prov = "sem resposta"
                st.session_state.last_info = "⚠️ IAs e web indisponíveis no momento"
                add_message(sid, "user", prompt)

            resposta_cofre = tratar_comando_cofre_chat(prompt)
        
            st.session_state.messages.append({"role": "assistant", "content": response})
            add_message(sid, "assistant", response)
            return response

    add_message(sid, "user", prompt)
    st.session_state.messages.append({"role": "assistant", "content": response})
    add_message(sid, "assistant", response)
    st.session_state.last_info = (
        f"✅ Respondeu: {prov}"
        + (f" (falhou: {'; '.join(erros)})" if erros else "")
        + (" · 🌐 web: " + fonte_web if fonte_web else "")
    )
    return response


# ============================================================
# RECONHECIMENTO (Whisper/Groq) E VOZ NEURAL (Edge TTS)
# ============================================================
def transcrever_audio(audio_bytes: bytes, mime: str = ""):
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
            data={"model": "whisper-large-v3-turbo", "language": "pt", "temperature": "0", "prompt": "Conversa em português do Brasil com o assistente Zeus."},
            timeout=60,
        )
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
# BUSCA NA INTERNET — v4.0 (Google + DDGS Multi-Busca + Wiki)
# ============================================================
WEB_TRIGGERS = (
    "hoje", "agora", "atual", "atualmente", "últimas", "ultimas", "notícia", "noticia", "recente",
    "pesquise", "pesquisa", "busque", "procure", "google", "internet", "na web", "cotação", "cotacao",
    "dólar", "dolar", "preço", "preco", "clima", "previsão", "previsao", "resultado", "placar",
    "quem ganhou", "quem é o atual", "lançamento", "lancamento", "versão mais nova", "ao vivo",
    "selic", "ipca", "cdi", "bitcoin", "btc", "eth", "ethereum", "solana", "cripto", "bolsa", "ibovespa",
)

_STOP_PREFIXOS = (
    "zeus", "ei zeus", "olá", "ola", "oi", "bom dia", "boa tarde", "boa noite",
    "por favor", "pfv", "pf", "me diga", "me diz", "me fale", "me fala",
    "você pode", "voce pode", "poderia", "pode", "gostaria", "queria", "quero",
    "pesquise", "pesquisa", "pesquisar", "busque", "busca", "buscar", "procure",
    "procura", "procurar", "verifique", "verifica", "confira", "consulte", "consulta",
    "google", "internet", "na web", "na internet", "no google",
)

_STOP_PALAVRAS = {
    "por favor", "pfv", "pf", "zeus", "ei", "olá", "ola", "oi", "bom dia", "boa tarde",
    "boa noite", "me", "mim", "vc", "voce", "você", "para", "pra", "que", "qual", "quais",
    "sobre", "aqui", "agora", "por gentileza", "obrigado", "obrigada", "grato",
    "faco", "faço", "fazer", "saber", "dizer", "falar", "trazer", "traga", "mostre",
    "mande", "envie", "fonte", "fontes", "link", "links",
}

_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]+",
    flags=re.UNICODE,
)


def _limpar_query(texto, max_len=160):
    if not texto:
        return ""
    q = str(texto)
    q = _RE_URL.sub(" ", q)
    q = _RE_EMOJI.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip()
    low = q.lower()
    mudou = True
    while mudou:
        mudou = False
        for p in _STOP_PREFIXOS:
            if low.startswith(p + " ") or low == p:
                q = q[len(p):].lstrip(" ,.:;!?-")
                low = q.lower()
                mudou = True
    tokens = [t for t in q.split() if t.lower().strip(".,;:!?") not in _STOP_PALAVRAS]
    q = " ".join(tokens).strip(" ,.;:!?-")
    return q[:max_len]


def _tokens_relevantes(texto, min_len=3):
    if not texto:
        return set()
    t = re.sub(r"[^\wÀ-ÿ\s]", " ", str(texto).lower())
    return {w for w in t.split() if len(w) >= min_len and w not in _STOP_PALAVRAS}


def _resultado_no_tema(query_limpa, resultado, limiar=0.30):
    q_tokens = _tokens_relevantes(query_limpa)
    r_tokens = _tokens_relevantes(resultado)
    if not q_tokens or not r_tokens:
        return False
    inter = q_tokens & r_tokens
    if len(q_tokens) == 1:
        return len(inter) == 1
    return (len(inter) / len(q_tokens)) >= limiar and len(inter) >= 1


def _reformular_query(q):
    if not q:
        return ""
    ruido = {
        "que", "qual", "quais", "quando", "onde", "como", "porque", "por", "para",
        "pra", "de", "do", "da", "dos", "das", "o", "a", "os", "as", "um", "uma",
        "é", "eh", "foi", "será", "sera", "está", "esta", "tem", "há", "ha", "hoje", "agora",
    }
    toks = [t for t in q.split() if t.lower().strip(".,;:!?") not in ruido]
    base = " ".join(toks).strip()[:110]
    if base and not re.search(r"\b20\d{2}\b", base):
        ano = time.strftime("%Y")
        return f"{base} {ano}"[:120]
    return base


# ------------------- buscadores -------------------

def _search_google(q, n=5):
    """Tenta buscar no Google (pode falhar em servidores de nuvem, mas tentamos)."""
    linhas = []
    try:
        from googlesearch import search as google_search
        for r in google_search(
            q,
            num_results=int(n or 5),
            lang="pt",
            region="br",
            advanced=True,
            sleep_interval=0.5,
            timeout=10,
        ):
            titulo = (getattr(r, "title", "") or "").strip()
            url = (getattr(r, "url", "") or "").strip()
            desc = (getattr(r, "description", "") or "").strip()
            if titulo and url:
                linhas.append(f"- {titulo}: {desc} ({url})")
    except Exception:
        pass
    return "\n".join(linhas)


def _search_multi(q, n=5):
    """Busca no Bing, DuckDuckGo e Yahoo usando ddgs."""
    linhas = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            resultados = ddgs.text(
                q,
                region="br-pt",
                safesearch="moderate",
                max_results=int(n or 5),
            )
            if resultados:
                for r in resultados:
                    titulo = (r.get("title") or "").strip()
                    link = (r.get("href") or "").strip()
                    corpo = (r.get("body") or "").strip()
                    if titulo and link:
                        linhas.append(f"- {titulo}: {corpo} ({link})")
    except Exception:
        pass
    return "\n".join(linhas)


def _search_wiki(q, n=4):
    try:
        r = requests.get(
            "https://pt.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": q,
                "format": "json",
                "srlimit": int(n or 4),
            },
            headers={"User-Agent": "ZeusBot/4.0 (https://streamlit.app)"},
            timeout=10,
        )
        if r.status_code != 200:
            return ""
        itens = r.json().get("query", {}).get("search", [])
        linhas = []
        for i in itens:
            titulo = (i.get("title") or "").strip()
            snip = re.sub(r"<[^>]+>", "", i.get("snippet") or "").strip()
            if titulo:
                slug = titulo.replace(" ", "_")
                linhas.append(f"- {titulo}: {snip} (https://pt.wikipedia.org/wiki/{slug})")
        return "\n".join(linhas)
    except Exception:
        return ""


# ------------------- orquestração -------------------

_FONTES_WEB = (
    ("Google", _search_google),
    ("Bing/DuckDuckGo/Yahoo", _search_multi),
    ("Wikipédia", _search_wiki),
)


def _executar_fontes(q, n, limiar=0.20):
    coletados = []
    fontes_usadas = []
    for nome, fn in _FONTES_WEB:
        try:
            txt = fn(q, n)
        except Exception:
            txt = ""
        txt = (txt or "").strip()
        if not txt:
            continue

        if "Wikipédia" in nome and "_resultado_no_tema" in globals():
            try:
                if not _resultado_no_tema(q, txt, limiar=limiar):
                    continue
            except Exception:
                pass

        coletados.append(f"[{nome}]\n{txt}")
        fontes_usadas.append(nome)
        if len("\n\n".join(coletados)) >= 1200:
            break

    return "\n\n".join(coletados), ", ".join(fontes_usadas)


def web_search(q, n=5):
    q_limpa = _limpar_query(q) if "_limpar_query" in globals() else (q or "").strip()
    if not q_limpa:
        return "", ""

    res_tavily = buscar_web_tavily(q_limpa, max_results=n)
    if res_tavily and res_tavily.strip():
        return res_tavily.strip(), "Tavily"

    txt, fonte = _executar_fontes(q_limpa, n, limiar=0.20)
    if txt:
        return txt, fonte

    if "_reformular_query" in globals():
        q2 = _reformular_query(q_limpa)
        if q2 and q2 != q_limpa:
            txt2, fonte2 = _executar_fontes(q2, n, limiar=0.15)
            if txt2:
                return txt2, f"{fonte2} (re-busca)"

    return "", ""


def precisa_web(prompt):
    modo = st.session_state.get("web_mode", "Automática")
    if modo == "Desligada":
        return False
    if modo == "Sempre":
        return True
    low = (prompt or "").lower()
    gatilhos = WEB_TRIGGERS if "WEB_TRIGGERS" in globals() else ()
    return (
        any(t in low for t in gatilhos)
        or bool(re.search(r"\b20(2[4-9]|3[0-9])\b", low))
        or len(low.split()) <= 5
    )


def buscar_web_tavily(query: str, max_results: int = 5) -> str:
    api_key = (
        (st.secrets.get("TAVILY_API_KEY") if hasattr(st, "secrets") else "")
        or os.getenv("TAVILY_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return ""
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": int(max_results or 5),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        partes = []
        if data.get("answer"):
            partes.append(f"Resumo direto: {data['answer']}")
        for item in data.get("results", []) or []:
            titulo = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            conteudo = (item.get("content") or "").strip()
            if titulo or conteudo:
                partes.append(f"- {titulo} ({url}): {conteudo}")
        return "\n".join(partes)
    except Exception:
        return ""


_REGRAS_WEB_LIVRE = (
    "INSTRUÇÕES DE RESPOSTA:\n"
    "1. Priorize os fatos presentes em [DADOS DA INTERNET] quando houver resultados relevantes.\n"
    "2. Se [DADOS DA INTERNET] estiver vazio, incompleto ou não trouxer a resposta exata, responda livremente usando seu conhecimento geral (LLMs) de forma clara e útil, sem dizer que falhou ou travou.\n"
    "3. Nunca invente URLs que não estejam no texto; se citar links, use apenas os que aparecerem.\n"
)


def enriquecer_com_web(prompt):
    hoje = time.strftime("%d/%m/%Y")
    if not precisa_web(prompt):
        return prompt, "llm_direto"

    ctx_web, fonte = web_search(prompt)
    if not ctx_web:
        novo = (
            f"[CONTEXTO WEB em {hoje}: sem resultados diretos na busca]\n"
            + _REGRAS_WEB_LIVRE
            + "\nResponda à pergunta do usuário da melhor forma possível usando seu conhecimento.\n"
            "Pergunta do usuário: " + (prompt or "")
        )
        return novo, "fallback_llm"

    novo = (
        f"[DADOS DA INTERNET em {hoje}, via {fonte}]\n"
        + ctx_web[:4500]
        + "\n[FIM DOS DADOS]\n\n"
        + _REGRAS_WEB_LIVRE
        + "\nPergunta do usuário: " + (prompt or "")
    )
    return novo, fonte
# ============================================================
# GERAÇÃO DE MÍDIA
# ============================================================
def _mime_img(b):
    return "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"


def _to_bytes(obj):
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    import io as _io
    buf = _io.BytesIO()
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
    for model in modelos:
        try:
            r = requests.post(
                f"https://router.huggingface.co/hf-inference/models/{model}",
                headers={"Authorization": "Bearer " + _secret("HUGGINGFACE_API_KEY")},
                json={"inputs": prompt}, timeout=120,
            )
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
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
        timeout=120,
    )
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
        r = requests.get(
            "https://image.pollinations.ai/prompt/" + quote(prompt[:600]),
            params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
            headers={"User-Agent": "Mozilla/5.0 (Zeus/3.0)"}, timeout=120,
        )
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
        ultimo = f"({r.status_code})"
        if r.status_code == 429 and tentativa == 0:
            time.sleep(16)
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
            r = requests.post(
                "https://api.deepinfra.com/v1/openai/images/generations",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=body, timeout=120,
            )
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
    for im in msg.get("images") or []:
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
    r = requests.post(
        OPENROUTER_BASE + "/chat/completions", headers=_or_headers(key),
        json={"model": modelo, "messages": [{"role": "user", "content": prompt}], "modalities": ["image", "text"]},
        timeout=120,
    )
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
    r = requests.post(
        OPENROUTER_BASE + "/chat/completions", headers=_or_headers(key),
        json={
            "model": modelo, "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": uri}}]}],
        },
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    return _or_extrair_imagem(r.json())


def _vis_openrouter(k, b, m, p):
    return _vis_openai(
        OPENROUTER_BASE + "/chat/completions", k, _secret("OPENROUTER_VISION_MODEL") or "google/gemini-2.5-flash",
        b, m, p, extra={"X-Title": "Zeus 3.0"},
    )


IMAGE_CASCADE = [
    ("DeepInfra", _img_deepinfra), ("OpenRouter", _img_openrouter), ("Hugging Face", _img_hf),
    ("Gemini", _img_gemini), ("Pollinations (sem chave)", _img_pollinations),
]


def make_image(prompt, log):
    for nome, fn in IMAGE_CASCADE:
        try:
            data = fn(prompt)
            log.append("✅ Imagem criada por: " + nome)
            return data
        except Exception as e:
            log.append("❌ Imagem " + nome + ": " + str(e)[:160])
    return None


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
    r = requests.post(
        "https://api.deepinfra.com/v1/images/edits",
        headers={"Authorization": "Bearer " + key},
        files={"image": ("foto.jpg", img, _mime_img(img))},
        data={"prompt": prompt, "model": model, "n": "1", "response_format": "b64_json"},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    d = r.json()["data"][0]
    if d.get("b64_json"):
        return base64.b64decode(d["b64_json"])
    if d.get("url"):
        return requests.get(d["url"], timeout=60).content
    raise RuntimeError("DeepInfra não devolveu imagem")


EDIT_CASCADE = [
    ("DeepInfra (edita a sua foto)", _edit_deepinfra), ("OpenRouter (edita a sua foto)", _edit_openrouter),
    ("Gemini (edita a sua foto)", _edit_gemini), ("Hugging Face (edita a sua foto)", _edit_hf),
]


def editar_imagem(img, prompt_edicao, descricao, log):
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


def make_video(prompt, log, img=None):
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
# PIPELINE MULTI-IA
# ============================================================
def _llm(prompt, system, log, etapa):
    ok, texto, prov, erros = get_llm_response(
        st.session_state.session_id, prompt, st.session_state.get("model_sel", "groq"), system=system, use_history=False,
    )
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
        resumo = _llm(
            "Pedido: " + pedido + "\n\nContexto da web:\n" + contexto[:4000],
            "Você é um pesquisador. Resuma em português, em até 8 linhas objetivas, os fatos úteis do contexto para atender ao pedido. Não invente nada.",
            log, "Resumo da pesquisa",
        )
    sys_b = (
        "Você é engenheiro de prompts para IAs de mídia. Responda EXATAMENTE em 2 linhas, sem mais nada:\n"
        "IMAGEM: <prompt detalhado em inglês: sujeito, estilo, iluminação, composição, cores, câmera>\n"
        "VIDEO: <prompt em inglês descrevendo ação e movimento de câmera em 5 segundos>"
    )
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
# VISÃO
# ============================================================
def _vis_openai(url, key, model, img_b64, mime, prompt, extra=None):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    r = requests.post(
        url, headers=headers,
        json={
            "model": model, "max_tokens": 700,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + img_b64}}]}],
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _vis_groq(k, b, m, p):
    return _vis_openai(
        "https://api.groq.com/openai/v1/chat/completions", k, _secret("GROQ_VISION_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct", b, m, p,
    )


def _gemini_generate(key, payload, modelos=None):
    modelos = modelos or ([x for x in [_secret("GEMINI_VISION_MODEL")] if x] + ["gemini-2.5-flash", "gemini-flash-latest"])
    ultimo = ""
    for model in modelos:
        for ver in ("v1", "v1beta"):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": key}, json=payload, timeout=90,
            )
            if r.status_code == 200:
                return "".join(x.get("text", "") for x in r.json()["candidates"][0]["content"]["parts"]).strip()
            ultimo = f"({r.status_code}) {r.text[:90]}"
            if r.status_code in (401, 403, 429):
                raise RuntimeError(ultimo)
    raise RuntimeError(ultimo)


def analisar_imagem(img_b64, mime, pergunta):
    key = _secret("GEMINI_API_KEY")
    if not key:
        return "❌ GEMINI_API_KEY ausente."
    try:
        return _gemini_generate(key, {"contents": [{"role": "user", "parts": [{"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]})
    except Exception as e:
        return "❌ Erro na análise: " + str(e)[:200]


def _vis_gemini(k, b, m, p):
    return _gemini_generate(k, {"contents": [{"role": "user", "parts": [{"text": p}, {"inline_data": {"mime_type": m, "data": b}}]}]})


def _vis_claude(k, b, m, p):
    model = _secret("ANTHROPIC_MODEL") or "claude-haiku-5-5"
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": k, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 900, "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": m, "data": b}}, {"type": "text", "text": p}]}]},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return "".join(x.get("text", "") for x in r.json()["content"]).strip()


def _vis_hf(k, b, m, p):
    return _vis_openai("https://router.huggingface.co/v1/chat/completions", k, _secret("HF_VISION_MODEL") or "Qwen/Qwen2.5-VL-7B-Instruct", b, m, p)


def _vis_github(k, b, m, p):
    return _vis_openai(
        "https://models.github.ai/inference/chat/completions", k, _secret("GITHUB_VISION_MODEL") or "openai/gpt-4.1-mini", b, m, p,
        extra={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )


def _vis_deepinfra(k, b, m, p):
    modelos = [x for x in [_secret("DEEPINFRA_VISION_MODEL")] if x] + ["Qwen/Qwen2.5-VL-32B-Instruct", "meta-llama/Llama-3.2-11B-Vision-Instruct"]
    ultimo = None
    for model in modelos:
        try:
            return _vis_openai("https://api.deepinfra.com/v1/openai/chat/completions", k, model, b, m, p)
        except Exception as e:
            ultimo = e
    raise ultimo


VISION_CASCADE = {
    "deepinfra": _vis_deepinfra, "openrouter": _vis_openrouter, "groq": _vis_groq,
    "gemini": _vis_gemini, "claude": _vis_claude, "huggingface": _vis_hf, "github": _vis_github,
}


def ask_vision(img_bytes, comando):
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
# ESTÚDIO
# ============================================================
ACOES = ["Descrever", "Editar foto", "Criar vídeo", "Editar foto + vídeo"]
ESTILOS_FOTO = {
    "Automático": "", "Realista HD": "ultra realistic, sharp details, natural skin texture, professional photography",
    "Cyberpunk neon": "cyberpunk neon lights, futuristic city glow, magenta and cyan rim light",
    "Anime": "anime style, vibrant colors, clean line art", "Pintura a óleo": "classic oil painting, visible brush strokes, rich textures",
    "Retrato de estúdio": "professional studio portrait, soft key light, shallow depth of field",
    "Preto e branco cinematográfico": "cinematic black and white, high contrast, film grain",
    "Fantasia épica": "epic fantasy art, dramatic lighting, magical atmosphere",
    "Cartoon 3D": "3D cartoon render, soft shading, vivid colors",
    "Aquarela": "delicate watercolor painting, soft bleeding colors, paper texture",
}
MOVIMENTOS_VIDEO = {
    "Automático": "", "Zoom lento (aproximar)": "slow zoom in", "Panorâmica lateral": "smooth horizontal pan",
    "Câmera orbitando": "slow orbiting camera around the subject", "Câmera na mão (realista)": "natural handheld camera shake",
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
    desc, prov, vlog = ask_vision(img, "Describe this photo in detail in English (people, clothing, pose, background, colors, lighting, framing). Max 8 lines.")
    log.extend(vlog)
    res["desc"] = desc or ""
    sys_p = (
        "You are a prompt engineer for image-editing and video AIs. Reply EXACTLY 2 lines, nothing else:\n"
        "EDIT: <instruction to edit the ORIGINAL photo as requested, keeping the person's identity, pose and composition>\n"
        "VIDEO: <5-second motion prompt: subtle animation and camera movement for this photo>"
    )
    bruto = _llm(
        "Photo description:\n" + (desc or "(unavailable)") + "\n\nUser request (Portuguese): " + (comando or "melhore esta foto")
        + ("\n\nRequired visual style: " + extra_estilo if extra_estilo else "") + ("\n\nCamera motion for the video: " + extra_mov if extra_mov else ""),
        sys_p, log, "Comando para as IAs de imagem",
    )
    m_e = re.search(r"EDIT:\s*(.+)", bruto or "")
    m_v = re.search(r"VIDEO:\s*(.+)", bruto or "")
    res["prompt_edit"] = m_e.group(1).strip() if m_e else ((comando or "enhance this photo, keep identity") + (", " + extra_estilo if extra_estilo else ""))
    res["prompt_vid"] = m_v.group(1).strip() if m_v else res["prompt_edit"]
    if acao in ("Editar foto", "Editar foto + vídeo"):
        res["img"], res["modo_img"] = editar_imagem(img, res["prompt_edit"], desc or "", log)
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
            st.session_state.foto_bytes = dados
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
    "Thalita multilíngue (pt-BR ♀)": "pt-BR-ThalitaMultilingualNeural", "Francisca (pt-BR ♀)": "pt-BR-FranciscaNeural",
    "Antonio (pt-BR ♂)": "pt-BR-AntonioNeural", "Raquel (pt-PT ♀)": "pt-PT-RaquelNeural", "Duarte (pt-PT ♂)": "pt-PT-DuarteNeural",
    "Ava multilíngue (en-US ♀)": "en-US-AvaMultilingualNeural", "Andrew multilíngue (en-US ♂)": "en-US-AndrewMultilingualNeural",
    "Emma multilíngue (en-US ♀)": "en-US-EmmaMultilingualNeural", "Brian multilíngue (en-US ♂)": "en-US-BrianMultilingualNeural",
    "Vivienne multilíngue (fr-FR ♀)": "fr-FR-VivienneMultilingualNeural", "Remy multilíngue (fr-FR ♂)": "fr-FR-RemyMultilingualNeural",
    "Seraphina multilíngue (de-DE ♀)": "de-DE-SeraphinaMultilingualNeural", "Florian multilíngue (de-DE ♂)": "de-DE-FlorianMultilingualNeural",
}


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
        return vozes or FALLBACK_VOZES_EXTRA
    except Exception:
        return FALLBACK_VOZES_EXTRA


# ============================================================
# AUTO-EVOLUÇÃO SEGURA + CRIADOR DE PASTAS (ZIP)
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
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", texto, re.S)
    return (m.group(1) if m else texto).strip()


def validar_codigo_habilidade(codigo):
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as e:
        return False, f"erro de sintaxe na linha {e.lineno}: {e.msg}"
    for no in arvore.body:
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(no, ast.Expr) and isinstance(getattr(no, "value", None), ast.Constant):
            continue
        if (isinstance(no, ast.Assign) and all(isinstance(t, ast.Name) for t in no.targets) and isinstance(no.value, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set))):
            continue
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
                json={"model": modelo, "max_tokens": max_tokens, "system": sistema, "messages": [{"role": "user", "content": pedido}]},
                timeout=120,
            )
        except Exception as e:
            return 503, "erro de conexão: " + str(e)[:100]
        if r.status_code == 200:
            return 200, "".join(b.get("text", "") for b in r.json()["content"])
        ultimo = (r.status_code, r.text[:200])
        if r.status_code not in (400, 404):
            break
    return ultimo


def _gemini_plano_b(sistema, pedido):
    key = _secret("GEMINI_API_KEY")
    if not key:
        return None
    try:
        return _gemini_generate(key, {"contents": [{"role": "user", "parts": [{"text": sistema + "\n\n" + pedido}]}]}, modelos=MODELS["gemini"])
    except Exception:
        return None


def gerar_com_prioridade_claude(sistema, pedido, max_tokens=2500):
    fila = st.session_state.setdefault("fila_habilidades", [])
    fila.append(pedido)
    aviso = st.empty()
    try:
        for tentativa in range(1, 4):
            code, texto = _claude_raw(sistema, pedido, max_tokens)
            if code == 200 and texto.strip():
                return texto, "Claude"
            if code not in TRANSITORIOS:
                break
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
        return {n: f for n, f in inspect.getmembers(mod, inspect.isfunction) if not n.startswith("_") and f.__module__ == mod.__name__}
    except Exception:
        return {}


def aprender_nova_habilidade(prompt_usuario):
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
    texto, prov = gerar_com_prioridade_claude(SYS_PROJETO, descricao, max_tokens=6000)
    if not texto:
        st.error("❌ Não foi possível gerar o código agora. Tente novamente mais tarde.")
        return None
    arquivos, avisos = _parse_arquivos(texto)
    if not arquivos:
        st.error("❌ A IA não devolveu arquivos no formato esperado. Tente reformular o pedido.")
        return None
    pasta = re.sub(r"[^a-z0-9]+", "_", descricao.lower()).strip("_")[:30] or "projeto_zeus"
    return {"pasta": pasta, "arquivos": arquivos, "avisos": avisos, "provedor": prov, "zip": montar_zip(pasta, arquivos)}


SECRETS_EXEMPLO = """GROQ_API_KEY = "cole_aqui"
GEMINI_API_KEY = "cole_aqui"
ANTHROPIC_API_KEY = "cole_aqui"
HUGGINGFACE_API_KEY = "cole_aqui"
GITHUB_API_KEY = "cole_aqui"      # token com Contents: Read and write (e Models: read)
GITHUB_USER = "seu_usuario"
GITHUB_REPO = "seu_repositorio"
DEEPINFRA_API_KEY = "cole_aqui"
OPENROUTER_API_KEY = "cole_aqui"
"""


def exportar_zeus_zip():
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
            pedido = st.text_area("O que o Zeus deve aprender a fazer?", key="evo_pedido", placeholder="Ex.: uma função que converte reais para dólar dado o valor e a cotação")
            if st.button("🧬 Aprender habilidade", key="evo_run") and pedido.strip():
                aprender_nova_habilidade(pedido.strip())
            ultima = st.session_state.get("ultima_habilidade")
            if ultima:
                st.caption("Última habilidade gerada por " + str(ultima["provedor"]) + ": " + ultima["pedido"][:80])
                st.code(ultima["codigo"], language="python")
                st.download_button(
                    "⬇️ Baixar pasta da habilidade (ZIP)",
                    montar_zip("zeus_habilidade", {"zeus_skills.py": ultima["codigo"] + "\n"}),
                    file_name="zeus_habilidade.zip", mime="application/zip", key="evo_dl_skill",
                )
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
            desc = st.text_area("Descreva o projeto que o Zeus deve criar", key="proj_desc", placeholder="Ex.: app Streamlit de lista de tarefas com SQLite, com requirements e README")
            if st.button("📦 Gerar pasta do projeto", key="proj_run") and desc.strip():
                with st.spinner("Criando a pasta do projeto..."):
                    st.session_state.projeto_zip = criar_projeto_zip(desc.strip())
            proj = st.session_state.get("projeto_zip")
            if proj:
                st.success(f"Pasta '{proj['pasta']}' criada por {proj['provedor']} com {len(proj['arquivos'])} arquivo(s).")
                for a in proj["avisos"]:
                    st.warning(a)
                st.download_button("⬇️ Baixar a pasta (ZIP)", proj["zip"], file_name=proj["pasta"] + ".zip", mime="application/zip", key="proj_dl")
                ver = st.selectbox("Pré-visualizar arquivo", list(proj["arquivos"].keys()), key="proj_ver")
                st.code(proj["arquivos"][ver], language=("python" if ver.endswith(".py") else None))
        with aba3:
            st.write("Baixe o Zeus inteiro como pasta pronta para subir no GitHub.")
            st.download_button("⬇️ Baixar zeus_projeto.zip", exportar_zeus_zip(), file_name="zeus_projeto.zip", mime="application/zip", key="zeus_dl")


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
function sendMsg(type, data) { window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*"); }
sendMsg("streamlit:componentReady", {apiVersion: 1});
sendMsg("streamlit:setFrameHeight", {height: 700});

const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
let avatar = "sphere", SILENCE = 2, STT = "whisper", TTS = "edge", TTS_RATE = 1;
let initialized = false, lastReplyId = 0, pendingEl = null, pendingUserEl = null;
let state = "idle", pulse = 0;
let micOn = false, pausedForBot = false, buffer = "", silenceTimer = null, silenceDeadline = 0, thinkTimer = null, lastAfter = 0;
let rec = null, mediaRec = null;
const TITLES = { sphere: "[ CYBER-SPHERE v3.0 ]", eye: "[ CYBER-EYE v3.0 ]", robot: "[ CYBER-BOT v3.0 ]", zeus: "[ ZEUS 3D ]" };
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
  setTimeout(function () { if (!started && state === "speaking") { window.speechSynthesis.cancel(); afterSpeak(); } }, 4000);
}
function afterSpeak() {
  const n = Date.now(); if (n - lastAfter < 600) return; lastAfter = n;
  setState("idle"); pausedForBot = false;
  if (!micOn) return;
  if (STT === "whisper") setTimeout(function () { if (micOn && stream) beginRecording(); }, 300);
  else setTimeout(function () { try { rec.start(); } catch (e) {} }, 400);
}

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
    if (dur < 350) { pausedForBot = false; setState("idle"); beginRecording(); return; }
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
function lg(x0, y0, x1, y1, stops) { const g = ctx.createLinearGradient(x0, y0, x1, y1); stops.forEach(function (s) { g.addColorStop(s[0], s[1]); }); return g; }
function rg(x, y, r0, r1, stops) { const g = ctx.createRadialGradient(x, y, r0, x, y, r1); stops.forEach(function (s) { g.addColorStop(s[0], s[1]); }); return g; }
function bolt(x, y, r) {
  ctx.fillStyle = rg(x - r * 0.3, y - r * 0.3, 0, r * 1.2, [[0, "#f6faff"], [0.6, "#8d9bb4"], [1, "#2b3347"]]);
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.65)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x - r * 0.6, y - r * 0.2); ctx.lineTo(x + r * 0.6, y + r * 0.2); ctx.stroke();
}

const PAL = ["#00e5ff", "#ff2bd6", "#3dffb0", "#ffb02e", "#8b5cff"];
const HUES = [190, 315, 150, 38, 265];
let palIdx = 0, startle = 0, ripples = [];
const ptr = { x: 0, y: 0, active: false, last: 0 };
function ptrXY(e) { const r = ((avatar === "sphere" || avatar === "zeus") ? document.getElementById("three-box") : cv).getBoundingClientRect(); ptr.x = e.clientX - r.left; ptr.y = e.clientY - r.top; ptr.active = true; ptr.last = performance.now(); }
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

function drawEye(t) {
  const cx = W / 2, cy = H * 0.36, R = Math.min(W * 0.26, H * 0.17), rad = Math.PI / 180, ac = accent(), hue = HUES[palIdx];
  const ls = state === "listening", th = state === "thinking";
  const dist = trackPointer(cx, cy, R * 3.2);
  const near = ptr.active ? Math.max(0, 1 - dist / (R * 4)) : 0, hov = dist < R * 1.8 ? 1 : 0;
  startle = Math.max(0, startle - 0.025);
  ctx.fillStyle = rg(cx, cy, R * 0.9, R * 2.5, [[0, "hsla(" + hue + ",100%,55%,0.30)"], [0.5, "hsla(" + (hue + 90) + ",100%,60%,0.12)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 2.5, 0, 7); ctx.fill();
  ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.85)"; ctx.shadowBlur = 26; ctx.shadowOffsetY = 10;
  ctx.fillStyle = lg(cx - R * 1.7, cy - R * 1.7, cx + R * 1.7, cy + R * 1.7, [[0, "#f2f7ff"], [0.22, "#93a4bf"], [0.5, "#2b354b"], [0.78, "#0e1321"], [1, "#6a7894"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.72, 0, 7); ctx.fill(); ctx.restore();
  ctx.strokeStyle = "rgba(255,255,255,0.5)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, R * 1.69, Math.PI * 0.95, Math.PI * 1.7); ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
  for (let i = 0; i < 26; i++) { ctx.beginPath(); ctx.arc(cx, cy, R * (1.52 + i * 0.007), 0, 7); ctx.stroke(); }
  for (let i = 0; i < 8; i++) { const a = (i * 45 + 22.5) * rad; bolt(cx + Math.cos(a) * R * 1.6, cy + Math.sin(a) * R * 1.6, R * 0.05); }
  ctx.fillStyle = lg(cx, cy - R * 1.5, cx, cy + R * 1.5, [[0, "#03050a"], [1, "#182034"]]);
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.5, 0, 7); ctx.fill();
  const span = (th ? 130 : ls ? 220 : 90) + hov * 90 + startle * 120;
  for (let i = 0; i < 48; i++) {
    const a0 = i * 7.5, lit = (((a0 - ring) % 360) + 360) % 360 < span;
    ctx.strokeStyle = lit ? "hsl(" + ((hue + i * 3) % 360) + ",100%,60%)" : "rgba(255,255,255,0.07)"; ctx.lineWidth = R * (0.07 + hov * 0.02);
    if (lit) glow(ac, 9);
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.38, a0 * rad, (a0 + 5) * rad); ctx.stroke(); noGlow();
  }
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
  ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.beginPath(); ctx.ellipse(ix - ir * 0.38 - look.x * ir * 0.08, iy - ir * 0.42 - look.y * ir * 0.06, ir * 0.16, ir * 0.1, -0.6, 0, 7); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.35)"; ctx.beginPath(); ctx.arc(ix + ir * 0.4, iy + ir * 0.45, ir * 0.05, 0, 7); ctx.fill();
  ctx.fillStyle = lg(cx - LR, cy - LR, cx + LR * 0.3, cy + LR * 0.3, [[0, "rgba(255,255,255,0.28)"], [0.5, "rgba(255,255,255,0.03)"], [1, "rgba(255,255,255,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = R * 0.05; ctx.beginPath(); ctx.arc(cx, cy, LR * 0.86, 0.2 * Math.PI, 0.8 * Math.PI); ctx.stroke();
  const cov = (1 - open) * LR, top = cy - LR + cov, bot = cy + LR - cov;
  ctx.fillStyle = lg(0, cy - LR * 1.1, 0, top + 0.01, [[0, "#232c42"], [1, "#8a9ab5"]]); ctx.fillRect(cx - LR * 1.1, cy - LR * 1.1, LR * 2.2, top - (cy - LR * 1.1));
  ctx.fillStyle = lg(0, bot, 0, cy + LR * 1.1, [[0, "#8a9ab5"], [1, "#232c42"]]); ctx.fillRect(cx - LR * 1.1, bot, LR * 2.2, cy + LR * 1.1 - bot);
  if (cov > 1) { ctx.strokeStyle = ac; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx - LR, top); ctx.lineTo(cx + LR, top); ctx.moveTo(cx - LR, bot); ctx.lineTo(cx + LR, bot); ctx.stroke(); }
  ctx.restore();
  ctx.strokeStyle = lg(cx - LR, cy - LR, cx + LR, cy + LR, [[0, "#e6eefc"], [0.5, "#4a5876"], [1, "#12192b"]]); ctx.lineWidth = R * 0.07;
  ctx.beginPath(); ctx.arc(cx, cy, LR, 0, 7); ctx.stroke();
  glow(ac, 10 + hov * 10); ctx.strokeStyle = ac; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, cy, LR - R * 0.05, 0, 7); ctx.stroke(); noGlow();
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

function drawRobot(t) {
  const cx = W / 2, cy = H * 0.34, S = Math.min(W * 0.5, H * 0.46), hw = S * 0.5, hh = S * 0.42, rad = Math.PI / 180;
  const ls = state === "listening", th = state === "thinking", sp = state === "speaking", ac = accent(), hue = HUES[palIdx];
  const dist = trackPointer(cx, cy - hh * 0.35, S * 1.5);
  const hovHead = ptr.active && Math.abs(ptr.x - cx) < hw * 1.2 && Math.abs(ptr.y - cy) < hh * 1.2 ? 1 : 0;
  startle = Math.max(0, startle - 0.025);
  const bob = Math.sin(t * 1.6) * S * 0.01;
  ctx.fillStyle = rg(cx, cy, 0, S * 1.2, [[0, "hsla(" + hue + ",100%,55%," + (0.16 + (ls ? 0.1 * Math.sin(t * 6) : 0) + hovHead * 0.08) + ")"], [0.6, "hsla(" + (hue + 80) + ",100%,60%,0.07)"], [1, "rgba(0,0,0,0)"]]);
  ctx.beginPath(); ctx.arc(cx, cy, S * 1.2, 0, 7); ctx.fill();
  for (let i = 0; i < 5; i++) { const a = t * 0.6 + i * 1.2566, ox = cx + Math.cos(a) * S * 0.95, oy = cy + Math.sin(a) * S * 0.5; glow(PAL[i], 10); ctx.fillStyle = PAL[i]; ctx.beginPath(); ctx.arc(ox, oy, 3, 0, 7); ctx.fill(); noGlow(); }
  ctx.save(); ctx.translate(0, bob);
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
  ctx.fillStyle = rg(cx, cy + hh * 1.15, 0, hw * 0.9, [[0, "rgba(0,0,0,0.5)"], [1, "rgba(0,0,0,0)"]]); ctx.fillRect(cx - hw, cy + hh * 0.9, hw * 2, hh * 0.6);
  ctx.fillStyle = lg(cx - hw * 0.32, 0, cx + hw * 0.32, 0, [[0, "#10151f"], [0.5, "#8c9bb6"], [1, "#10151f"]]);
  ctx.fillRect(cx - hw * 0.32, cy + hh * 0.8, hw * 0.64, hh * 0.55);
  ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.lineWidth = 2;
  for (let i = 0; i < 4; i++) { const y = cy + hh * (0.95 + i * 0.1); ctx.beginPath(); ctx.moveTo(cx - hw * 0.32, y); ctx.lineTo(cx + hw * 0.32, y); ctx.stroke(); }
  ctx.lineWidth = 3; ctx.strokeStyle = PAL[1]; ctx.beginPath(); ctx.moveTo(cx - hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx - hw * 0.55, cy + hh * 1.25, cx - hw * 0.6, cy + hh * 1.1, cx - hw * 0.7, ty + hh * 0.2); ctx.stroke();
  ctx.strokeStyle = PAL[0]; ctx.beginPath(); ctx.moveTo(cx + hw * 0.2, cy + hh * 0.9); ctx.bezierCurveTo(cx + hw * 0.55, cy + hh * 1.25, cx + hw * 0.6, cy + hh * 1.1, cx + hw * 0.7, ty + hh * 0.2); ctx.stroke();
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
  ctx.lineWidth = 1.5;
  [-1, 1].forEach(function (s) {
    ctx.strokeStyle = s < 0 ? "rgba(255,43,214,0.7)" : "rgba(0,229,255,0.7)";
    ctx.beginPath(); ctx.moveTo(cx + s * hw * 0.92, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.66, cy + hh * 0.12); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.3); ctx.lineTo(cx + s * hw * 0.55, cy + hh * 0.42); ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(cx + s * hw * 0.55, cy + hh * 0.42, 3, 0, 7); ctx.fill();
  });
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
  ctx.restore();
  ctx.restore();
  drawPointerFx(t, ac);
  ctx.fillStyle = "rgba(0,0,0,0.12)"; for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1);
}

const HAS3 = typeof THREE !== "undefined";
const box = document.getElementById("three-box");
let scene, camera, renderer, sphereGroup, wireMaterial, pointLight1, pointLight2, particlesMesh, rings = [], clock;
let col3, coreMesh, glowShell, arcLines, arcMat, frameN = 0; const sats = [];
let zeusGroup, raioMat, zRaio;
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

  // ---- ZEUS 3D (torso, cabeca, barba, olhos, raio) ----
  zeusGroup = new THREE.Group(); zeusGroup.visible = false; scene.add(zeusGroup);

  const peleMat = new THREE.MeshStandardMaterial({ color: 0xc9966a, roughness: 0.6, metalness: 0.05 });
  const mantoMat = new THREE.MeshStandardMaterial({ color: 0xb08d57, roughness: 0.5, metalness: 0.3 });
  const barbaMat = new THREE.MeshStandardMaterial({ color: 0xe6e6e6, roughness: 0.8, metalness: 0.0 });

  const zCorpo = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 1.35, 2.6, 24), mantoMat);
  zCorpo.position.y = -0.1; zeusGroup.add(zCorpo);

  const zCabeca = new THREE.Mesh(new THREE.SphereGeometry(0.62, 32, 32), peleMat);
  zCabeca.position.y = 1.55; zeusGroup.add(zCabeca);

  const zBarba = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.05, 20), barbaMat);
  zBarba.position.set(0, 0.95, 0.08); zBarba.rotation.x = Math.PI; zeusGroup.add(zBarba);

  const zCabelo = new THREE.Mesh(new THREE.SphereGeometry(0.68, 20, 20, 0, Math.PI * 2, 0, Math.PI * 0.62), barbaMat);
  zCabelo.position.y = 1.62; zeusGroup.add(zCabelo);

  const olhoGeo = new THREE.SphereGeometry(0.075, 12, 12);
  const zOlhoEsq = new THREE.Mesh(olhoGeo, new THREE.MeshStandardMaterial({ color: 0x00e5ff, emissive: 0x00e5ff, emissiveIntensity: 2 }));
  zOlhoEsq.position.set(-0.2, 1.6, 0.52); zeusGroup.add(zOlhoEsq);
  const zOlhoDir = zOlhoEsq.clone(); zOlhoDir.position.x = 0.2; zeusGroup.add(zOlhoDir);
  const zLuzOlhos = new THREE.PointLight(0x00e5ff, 1.2, 3); zLuzOlhos.position.set(0, 1.6, 0.6); zeusGroup.add(zLuzOlhos);

  const bracoGeo = new THREE.CylinderGeometry(0.16, 0.13, 1.25, 14);
  const zBracoEsq = new THREE.Mesh(bracoGeo, peleMat);
  zBracoEsq.position.set(-1.05, 0.55, 0); zBracoEsq.rotation.z = Math.PI * 0.18; zeusGroup.add(zBracoEsq);
  const zBracoDir = new THREE.Mesh(bracoGeo, peleMat);
  zBracoDir.position.set(1.0, 1.15, 0); zBracoDir.rotation.z = -Math.PI * 0.62; zeusGroup.add(zBracoDir);

  // raio: forma em zigue-zague extrudada, com brilho aditivo
  const raioShape = new THREE.Shape();
  raioShape.moveTo(0, 0.55); raioShape.lineTo(-0.16, 0.1); raioShape.lineTo(-0.02, 0.1);
  raioShape.lineTo(-0.2, -0.5); raioShape.lineTo(0.2, 0.0); raioShape.lineTo(0.04, 0.0); raioShape.lineTo(0, 0.55);
  const raioGeo = new THREE.ExtrudeGeometry(raioShape, { depth: 0.04, bevelEnabled: false });
  raioMat = new THREE.MeshBasicMaterial({ color: 0xfff2a0, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending });
  zRaio = new THREE.Mesh(raioGeo, raioMat);
  zRaio.scale.set(1.3, 1.3, 1.3); zRaio.position.set(1.55, 1.85, 0.05); zRaio.rotation.z = -0.3;
  zeusGroup.add(zRaio);

  zeusGroup.position.y = -0.4;
}
function applyAvatar() {
  document.getElementById("title").textContent = TITLES[avatar] || TITLES.sphere;
  box.style.display = (avatar === "sphere" || avatar === "zeus") ? "block" : "none";
  cv.style.display = (avatar === "sphere" || avatar === "zeus") ? "none" : "block";
  if (sphereGroup) sphereGroup.visible = (avatar === "sphere");
  if (zeusGroup) zeusGroup.visible = (avatar === "zeus");
  onResize(); setTimeout(onResize, 300);
}
window.addEventListener("resize", onResize); onResize(); setTimeout(onResize, 300); setTimeout(onResize, 1000);

let last = performance.now(); const t0 = last;
function updateZeus(t, dt) {
  const th = state === "thinking", sp = state === "speaking", ls = state === "listening";
  zeusGroup.rotation.y = look.x * 0.25;
  zeusGroup.position.y = -0.4 + Math.sin(t * 1.3) * 0.03;
  const piscaOlho = 0.75 + 0.25 * Math.sin(t * (ls ? 10 : 3));
  zOlhoEsq.material.emissiveIntensity = piscaOlho * (th ? 1.6 : 1);
  zOlhoDir.material.emissiveIntensity = piscaOlho * (th ? 1.6 : 1);
  zLuzOlhos.intensity = 0.8 + piscaOlho * 0.6;
  const cintilar = sp ? (0.7 + 0.3 * Math.abs(Math.sin(t * 14))) : ls ? (0.6 + 0.4 * pulse) : (0.5 + 0.2 * Math.sin(t * 5));
  raioMat.opacity = Math.min(1, cintilar);
  const escalaRaio = 1.3 + (sp || ls ? 0.08 * Math.abs(Math.sin(t * 10)) : 0);
  zRaio.scale.set(escalaRaio, escalaRaio, escalaRaio);
  camera.lookAt(0, 0.6, 0);
}

function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.1, (now - last) / 1000); last = now; const t = (now - t0) / 1000;
  updateCommon(now, dt, t);
  if (avatar === "zeus") {
    if (!HAS3) return;
    trackPointer(W / 2 || 150, H * 0.4 || 150, 220);
    updateZeus(t, dt);
    renderer.render(scene, camera);
  } else if (avatar === "sphere") {
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


AVATAR_MAP = {
    "Esfera 3D (Cyberpunk)": "sphere",
    "Olho Cibernético": "eye",
    "Robô Digital": "robot",
    "Zeus Mitológico 3D (com raios)": "zeus",
}


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
    theme_name = st.selectbox("🎨 Interface", list(THEMES.keys()), index=0, key="zeus_theme_selector_main")
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
        _filtro = ({k_: v_ for k_, v_ in _todas.items() if "Multilingual" in v_} if grupo_voz.startswith("Multi") else _todas)
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
          
# ============================================================
# ZEUS FINAL - NUCLEO CONSOLIDADO UNICO
# (substitui TODOS os blocos "BLOCO SUPLEMENTAR ZEUS" anteriores:
#  3.5, 3.9, 4.1, 4.2, 4.3, 4.4 - apague-os do app.py e cole so este)
# Database no GitHub agora aponta para a pasta exclusiva: zilsy/
# ============================================================
import base64 as _b64_zf
import requests as _req_zf
import re as _re_zf
import ast as _ast_zf
import inspect as _insp_zf

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None

_firestore_zf = None
_firebase_admin_zf = None
_cred_zf = None
_db_firestore_zf = None
try:
    from google.cloud import firestore as _firestore_zf
    import firebase_admin as _firebase_admin_zf
    from firebase_admin import credentials as _cred_zf, firestore as _db_firestore_zf
except ImportError:
    pass

_ZF_PASTA_RAIZ = "zilsy"
_ZF_PASTA_MODULOS = _ZF_PASTA_RAIZ + "/modulos"


# ------------------------------------------------------------
# 1. GITHUB COMO DATABASE (pasta zilsy/)
# ------------------------------------------------------------
def zf_salvar_codigo_github(caminho_arquivo: str, codigo_conteudo: str, mensagem: str = "Zeus: atualizacao"):
    token = st.secrets.get("GITHUB_API_KEY") or st.secrets.get("GITHUB_TOKEN")
    user = st.secrets.get("GITHUB_USER")
    repo = st.secrets.get("GITHUB_REPO")
    if not (token and user and repo):
        return {"status": "erro", "mensagem": "Faltam chaves do GitHub nos Secrets."}

    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp_get = _req_zf.get(url, headers=headers, timeout=20)
        sha = resp_get.json().get("sha") if resp_get.status_code == 200 else None
        payload = {"message": mensagem, "content": _b64_zf.b64encode(codigo_conteudo.encode("utf-8")).decode("utf-8")}
        if sha:
            payload["sha"] = sha
        resp_put = _req_zf.put(url, headers=headers, json=payload, timeout=20)
        if resp_put.status_code in (200, 201):
            return {"status": "sucesso", "mensagem": f"Database GitHub atualizado em {caminho_arquivo}.",
                    "url": resp_put.json().get("content", {}).get("html_url", "")}
        try:
            detalhe = resp_put.json().get("message", "Erro na API do GitHub.")
        except Exception:
            detalhe = resp_put.text[:150]
        return {"status": "erro", "mensagem": f"Erro GitHub ({resp_put.status_code}): {detalhe}"}
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro de conexao: {str(e)[:150]}"}


def zf_listar_memoria_github(pasta: str = None):
    pasta = pasta or _ZF_PASTA_MODULOS
    token = st.secrets.get("GITHUB_API_KEY") or st.secrets.get("GITHUB_TOKEN")
    user = st.secrets.get("GITHUB_USER")
    repo = st.secrets.get("GITHUB_REPO")
    if not (token and user and repo):
        return []
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{pasta}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = _req_zf.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            itens = resp.json()
            if isinstance(itens, list):
                return [item["path"] for item in itens if item.get("type") == "file"]
    except Exception:
        pass
    return []


def zf_ler_arquivo_github(caminho_arquivo: str):
    token = st.secrets.get("GITHUB_API_KEY") or st.secrets.get("GITHUB_TOKEN")
    user = st.secrets.get("GITHUB_USER")
    repo = st.secrets.get("GITHUB_REPO")
    if not (token and user and repo):
        return None
    url = f"https://api.github.com/repos/{user}/{repo}/contents/{caminho_arquivo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = _req_zf.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            dados = resp.json()
            if "content" in dados:
                return _b64_zf.b64decode(dados["content"]).decode("utf-8")
    except Exception:
        pass
    return None


def _zf_sanitizar_pasta(caminho: str, default: str = None) -> str:
    default = default or _ZF_PASTA_MODULOS
    partes = [p for p in caminho.strip().strip("/").split("/") if p not in ("", ".", "..")]
    return "/".join(partes) or default


def zf_salvar_firestore(colecao: str, doc_id: str, dados: dict):
    if not (_firebase_admin_zf and _db_firestore_zf and _cred_zf):
        return False, "Modulo Firebase nao disponivel."
    try:
        if not _firebase_admin_zf._apps:
            if "FIREBASE_CREDENTIALS" in st.secrets:
                import json
                cred_dict = json.loads(st.secrets["FIREBASE_CREDENTIALS"])
                cred = _cred_zf.Certificate(cred_dict)
                _firebase_admin_zf.initialize_app(cred)
            else:
                return False, "Credenciais do Firebase ausentes nos Secrets."
        db = _db_firestore_zf.client()
        db.collection(colecao).document(doc_id).set(dados, merge=True)
        return True, "Dados salvos no Firestore com sucesso."
    except Exception as e:
        return False, f"Firestore: {str(e)[:120]}"


# ------------------------------------------------------------
# 2. ENSEMBLE DE APRENDIZADO (multiplas IAs se revisam)
# ------------------------------------------------------------
_ZF_EXTENSOES = {"python": "py", "javascript": "js", "cpp": "cpp", "c++": "cpp", "java": "java"}


def _zf_extrair_codigo(texto: str) -> str:
    m = _re_zf.search(r"```(?:[a-zA-Z+]*)?\s*\n(.*?)```", texto, _re_zf.S)
    return (m.group(1) if m else texto).strip()


def _zf_executar_python(codigo: str):
    try:
        _ast_zf.parse(codigo)
    except SyntaxError as e:
        return False, f"Erro de sintaxe na linha {e.lineno}: {e.msg}"

    namespace = {"__builtins__": __builtins__}
    try:
        exec(codigo, namespace)
    except Exception as e:
        return False, f"Erro no carregamento: {str(e)[:150]}"

    funcoes = [v for k, v in namespace.items() if callable(v) and not k.startswith("_") and _insp_zf.isfunction(v)]
    if not funcoes:
        return True, "Codigo compilado e estruturado com sucesso."

    alvo = funcoes[0]
    try:
        params = _insp_zf.signature(alvo).parameters
        if params:
            return True, f"Funcao {alvo.__name__} validada (requer parametros: {chr(44).join(params)})."
        resultado = alvo()
        return True, f"Execucao bem-sucedida! Retorno: {resultado}"
    except Exception as e:
        return False, f"Erro ao executar a funcao: {str(e)[:150]}"


def _zf_chamar_ia(prompt: str, sistema: str, preferir: str = None):
    fn_prioridade = globals().get("gerar_com_prioridade_claude")
    if fn_prioridade and not preferir:
        return fn_prioridade(sistema, prompt, max_tokens=2000)
    fn_llm = globals().get("get_llm_response")
    if fn_llm and "session_id" in st.session_state:
        provedor = preferir or st.session_state.get("model_sel", "groq")
        ok, texto, prov, _erros = fn_llm(st.session_state.session_id, prompt, provedor, system=sistema, use_history=False)
        return (texto, prov) if ok else (None, None)
    return None, None


def zf_aprender_ensemble(instrucao: str, linguagem: str = "python") -> str:
    linguagem = linguagem.lower().strip()
    ext = _ZF_EXTENSOES.get(linguagem, "txt")

    sys_gerar = (
        f"Voce e um engenheiro senior em {linguagem}. Escreva codigo funcional, limpo e comentado "
        f"em {linguagem} puro que implemente exatamente o pedido. Responda APENAS com o codigo, sem explicacoes."
    )
    texto1, prov1 = _zf_chamar_ia("Pedido: " + instrucao, sys_gerar)
    if not texto1:
        return "Nenhuma IA disponivel gerou o codigo inicial."
    codigo_v1 = _zf_extrair_codigo(texto1)

    provedores_disp = globals().get("PROVIDERS", [])
    segundo_provedor = next((p for p in provedores_disp if p != prov1), None)

    sys_revisar = (
        f"Voce e um revisor de codigo senior em {linguagem}. Receba um codigo e melhore-o: "
        "corrija bugs, otimize e deixe mais claro, mantendo a mesma funcionalidade. "
        "Responda APENAS com o codigo final melhorado, sem explicacoes."
    )
    texto2, prov2 = _zf_chamar_ia("Codigo para revisar e melhorar:\n\n" + codigo_v1, sys_revisar, preferir=segundo_provedor)
    codigo_final = _zf_extrair_codigo(texto2) if texto2 else codigo_v1
    prov_revisor = prov2 if texto2 else None

    slug = "".join(c if c.isalnum() else "_" for c in instrucao[:25]).lower().strip("_") or "modulo"
    caminho = f"{_ZF_PASTA_MODULOS}/modulo_{slug}.{ext}"
    res_save = zf_salvar_codigo_github(caminho, codigo_final, f"Zeus ensemble ({linguagem}): {instrucao[:40]}")

    linhas_resp = []
    if linguagem == "python":
        ok_exec, msg_exec = _zf_executar_python(codigo_final)
        linhas_resp.append(("Execucao: " + msg_exec) if ok_exec else ("Aviso na execucao: " + msg_exec))
    else:
        linhas_resp.append(f"Codigo em {linguagem} gerado e salvo. Execucao automatica disponivel so para Python.")

    if res_save["status"] == "sucesso":
        extra_url = (" Ver: " + res_save["url"]) if res_save.get("url") else ""
        linhas_resp.append(f"GitHub: {caminho} (gerado por {prov1}" +
                            (f", revisado por {prov_revisor})" if prov_revisor else ")") + extra_url)
    else:
        linhas_resp.append("Erro ao salvar: " + res_save["mensagem"])

    zf_salvar_firestore("zeus_memoria", f"modulo_{slug}", {
        "instrucao": instrucao, "linguagem": linguagem, "caminho": caminho,
        "gerado_por": prov1, "revisado_por": prov_revisor,
    })

    audio = zf_falar("Modulo revisado por mais de uma inteligencia artificial e salvo com sucesso.")
    if audio:
        st.audio(audio, format="audio/mp3")

    return "\n\n".join(linhas_resp)


# ------------------------------------------------------------
# 3. MIDIA APRIMORADA
# ------------------------------------------------------------
def zf_gerar_imagem_aprimorada(prompt_base: str, log: list):
    fn_make_image = globals().get("make_image")
    fn_editar = globals().get("editar_imagem")
    if not fn_make_image:
        return None
    img = fn_make_image(prompt_base, log)
    if img and fn_editar:
        img_aprimorada, modo = fn_editar(
            img,
            "enhance resolution, sharpen fine details, improve lighting and color depth, "
            "professional post-processing, keep composition identical",
            prompt_base, log,
        )
        if img_aprimorada:
            return img_aprimorada
    return img


def zf_gerar_video_aprimorado(prompt_base: str, log: list):
    fn_make_video = globals().get("make_video")
    img_base = zf_gerar_imagem_aprimorada(prompt_base, log)
    if fn_make_video:
        return fn_make_video(prompt_base, log, img_base), img_base
    return None, img_base


# ------------------------------------------------------------
# 4. AVATARES E VOZ
# ------------------------------------------------------------
ZF_AVATAR_CLASSICO = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMDAgMzAwIj4KPGRlZnM+CjxyYWRpYWxHcmFkaWVudCBpZD0iYmcxIiBjeD0iNTAlIiBjeT0iMzAlIiByPSI4MCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjM2EzZjcwIi8+CjxzdG9wIG9mZnNldD0iNTAlIiBzdG9wLWNvbG9yPSIjMTUxODJlIi8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iIzA0MDUwYSIvPgo8L3JhZGlhbEdyYWRpZW50Pgo8bGluZWFyR3JhZGllbnQgaWQ9InJhaW8xIiB4MT0iMCIgeTE9IjAiIHgyPSIxIiB5Mj0iMSI+CjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNmZmZmZmYiLz4KPHN0b3Agb2Zmc2V0PSI0MCUiIHN0b3AtY29sb3I9IiNmZmY0YjAiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZmZhNzAwIi8+CjwvbGluZWFyR3JhZGllbnQ+CjxyYWRpYWxHcmFkaWVudCBpZD0iZ2xvdzEiIGN4PSI1MCUiIGN5PSI1MCUiIHI9IjUwJSI+CjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNmZmY1YjAiIHN0b3Atb3BhY2l0eT0iMC45Ii8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iI2ZmZjViMCIgc3RvcC1vcGFjaXR5PSIwIi8+CjwvcmFkaWFsR3JhZGllbnQ+CjwvZGVmcz4KPHJlY3Qgd2lkdGg9IjMwMCIgaGVpZ2h0PSIzMDAiIGZpbGw9InVybCgjYmcxKSIvPgo8Y2lyY2xlIGN4PSIxNjAiIGN5PSIxMzAiIHI9IjExMCIgZmlsbD0idXJsKCNnbG93MSkiLz4KPHBhdGggZD0iTTQwIDIzMCBDNjAgMTgwIDgwIDE0MCAxMTAgMTEwIEMxMzAgOTAgMTQwIDcwIDE0MCA3MCBMMTYwIDkwIEMxNjAgOTAgMTQ1IDExMCAxMjUgMTMwIEM5NSAxNjAgNzAgMjAwIDQ1IDI0MCBaIiBmaWxsPSIjYzlhMjZkIiBzdHJva2U9IiM4ZTZmNDMiIHN0cm9rZS13aWR0aD0iMiIvPgo8ZWxsaXBzZSBjeD0iMTIwIiBjeT0iMjIwIiByeD0iNzAiIHJ5PSI0NSIgZmlsbD0iI2U1ZTVlNSIvPgo8cGF0aCBkPSJNODAgMTkwIFExMjAgMTYwIDE2MCAxOTAgUTE0MCAyMzAgMTIwIDI0MCBROTAgMjMwIDgwIDE5MCBaIiBmaWxsPSIjZjVmNWY1Ii8+CjxjaXJjbGUgY3g9IjE1MCIgY3k9IjEwMCIgcj0iNDAiIGZpbGw9IiNkNGEzNzQiLz4KPHBhdGggZD0iTTExNSA3MCBRMTUwIDQ1IDE4NSA3MCBRMTkwIDU1IDE3NSA0NSBRMTUwIDMwIDEyNSA0NSBRMTEwIDU1IDExNSA3MCBaIiBmaWxsPSIjZThlOGU4Ii8+CjxwYXRoIGQ9Ik0xMTAgOTUgUTEwMCAxMzAgMTE1IDE1NSBRMTA1IDE0MCAxMDAgMTEwIFoiIGZpbGw9IiNlOGU4ZTgiLz4KPHBhdGggZD0iTTE5MCA5NSBRMjAwIDEzMCAxODUgMTU1IFExOTUgMTQwIDIwMCAxMTAgWiIgZmlsbD0iI2U4ZThlOCIvPgo8Y2lyY2xlIGN4PSIxMzUiIGN5PSIxMDAiIHI9IjUiIGZpbGw9IiMwMGU1ZmYiLz4KPGNpcmNsZSBjeD0iMTY1IiBjeT0iMTAwIiByPSI1IiBmaWxsPSIjMDBlNWZmIi8+CjxwYXRoIGQ9Ik0xMzAgMTIwIFExNTAgMTMwIDE3MCAxMjAiIHN0cm9rZT0iIzZiNGIyZSIgc3Ryb2tlLXdpZHRoPSIzIiBmaWxsPSJub25lIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggZD0iTTIwMCA4MCBDMjIwIDcwIDI0MCA5MCAyMzUgMTIwIEMyNTAgMTAwIDI2MCAxMzAgMjMwIDE1MCBDMjUwIDE2MCAyMjAgMTgwIDE5MCAxNjAgWiIgZmlsbD0iI2M5YTI2ZCIgc3Ryb2tlPSIjOGU2ZjQzIiBzdHJva2Utd2lkdGg9IjIiLz4KPHBhdGggZD0iTTIwNSA3MCBMMTY1IDE0MCBMMTk1IDE0MCBMMTc1IDIxMCBMMjQ1IDEyNSBMMjEwIDEyNSBaIiBmaWxsPSJ1cmwoI3JhaW8xKSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lkdGg9IjIiLz4KPHBhdGggZD0iTTIwNSA3MCBMMTY1IDE0MCBMMTk1IDE0MCBMMTc1IDIxMCBMMjQ1IDEyNSBMMjEwIDEyNSBaIiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZjZTAiIHN0cm9rZS13aWR0aD0iMSIgb3BhY2l0eT0iMC44Ii8+CjxjaXJjbGUgY3g9IjIwNSIgY3k9IjEzMCIgcj0iNDUiIGZpbGw9IiNmZmY1YjAiIG9wYWNpdHk9IjAuMyIvPgo8L3N2Zz4K"
ZF_AVATAR_ACAO = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMDAgMzAwIj4KPGRlZnM+CjxyYWRpYWxHcmFkaWVudCBpZD0iYmcyIiBjeD0iNTAlIiBjeT0iMjUlIiByPSI5MCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjMmIyZjVlIi8+CjxzdG9wIG9mZnNldD0iNDUlIiBzdG9wLWNvbG9yPSIjMTIxNDI4Ii8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iIzAyMDIwOCIvPgo8L3JhZGlhbEdyYWRpZW50Pgo8bGluZWFyR3JhZGllbnQgaWQ9InJhaW9BIiB4MT0iMCIgeTE9IjAiIHgyPSIxIiB5Mj0iMSI+CjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNmZmZmZmYiLz4KPHN0b3Agb2Zmc2V0PSIzNSUiIHN0b3AtY29sb3I9IiNmZmYwYTAiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZmY5NTAwIi8+CjwvbGluZWFyR3JhZGllbnQ+CjxsaW5lYXJHcmFkaWVudCBpZD0icmFpb0IiIHgxPSIxIiB5MT0iMCIgeDI9IjAiIHkyPSIxIj4KPHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZmZmZiIvPgo8c3RvcCBvZmZzZXQ9IjUwJSIgc3RvcC1jb2xvcj0iI2ZmZTQ5YSIvPgo8c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiNmZjdhMDAiLz4KPC9saW5lYXJHcmFkaWVudD4KPHJhZGlhbEdyYWRpZW50IGlkPSJnbG93QSIgY3g9IjUwJSIgY3k9IjUwJSIgcj0iNTAlIj4KPHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZWZiMCIgc3RvcC1vcGFjaXR5PSIwLjk1Ii8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iI2ZmZWZiMCIgc3RvcC1vcGFjaXR5PSIwIi8+CjwvcmFkaWFsR3JhZGllbnQ+CjxyYWRpYWxHcmFkaWVudCBpZD0ic2tpblNoYWRlIiBjeD0iNDAlIiBjeT0iMzUlIiByPSI2NSUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZTNiNTg4Ii8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iI2I5ODc1YSIvPgo8L3JhZGlhbEdyYWRpZW50Pgo8L2RlZnM+CjxyZWN0IHdpZHRoPSIzMDAiIGhlaWdodD0iMzAwIiBmaWxsPSJ1cmwoI2JnMikiLz4KPGNpcmNsZSBjeD0iMTUwIiBjeT0iMTQwIiByPSIxMzAiIGZpbGw9InVybCgjZ2xvd0EpIiBvcGFjaXR5PSIwLjYiLz4KPHBhdGggZD0iTTYwIDI2MCBDNzAgMjEwIDkwIDE3MCAxMTUgMTQwIEMxMDAgMTUwIDg1IDE1NSA3MCAxNTAgQzk1IDEzMCAxMTUgMTEwIDEzMCA4NSBMMTUwIDEwMCBDMTQwIDEyMCAxMjUgMTQwIDExMCAxNjAgQzEzMCAxNTAgMTUwIDE1NSAxNjUgMTc1IEMxNDAgMjAwIDEwMCAyMzAgNjAgMjYwIFoiIGZpbGw9IiNiOTg3NWEiIHN0cm9rZT0iIzdhNWEzNyIgc3Ryb2tlLXdpZHRoPSIyIi8+CjxlbGxpcHNlIGN4PSIxMjUiIGN5PSIyNDUiIHJ4PSI4NSIgcnk9IjUwIiBmaWxsPSIjZGNkY2RjIi8+CjxwYXRoIGQ9Ik03NSAyMTAgUTEyNSAxNzUgMTc1IDIxMCBRMTU1IDI1NSAxMjUgMjY1IFE5NSAyNTUgNzUgMjEwIFoiIGZpbGw9IiNmMmYyZjIiLz4KPGNpcmNsZSBjeD0iMTUwIiBjeT0iMTEwIiByPSI0NiIgZmlsbD0idXJsKCNza2luU2hhZGUpIi8+CjxwYXRoIGQ9Ik0xMDggNzUgUTE1MCA0MiAxOTIgNzUgUTE5OCA1NSAxNzggNDIgUTE1MCAyNCAxMjIgNDIgUTEwMiA1NSAxMDggNzUgWiIgZmlsbD0iI2VjZWNlYiIvPgo8cGF0aCBkPSJNMTAyIDEwMCBRODggMTQ1IDEwOCAxNzUgUTk0IDE1MCA5MCAxMTUgWiIgZmlsbD0iI2VjZWNlYiIvPgo8cGF0aCBkPSJNMTk4IDEwMCBRMjEyIDE0NSAxOTIgMTc1IFEyMDYgMTUwIDIxMCAxMTUgWiIgZmlsbD0iI2VjZWNlYiIvPgo8Y2lyY2xlIGN4PSIxMzQiIGN5PSIxMTAiIHI9IjYiIGZpbGw9IiMxOWYyZmYiLz4KPGNpcmNsZSBjeD0iMTY2IiBjeT0iMTEwIiByPSI2IiBmaWxsPSIjMTlmMmZmIi8+CjxjaXJjbGUgY3g9IjEzNCIgY3k9IjExMCIgcj0iMTAiIGZpbGw9IiMxOWYyZmYiIG9wYWNpdHk9IjAuMyIvPgo8Y2lyY2xlIGN4PSIxNjYiIGN5PSIxMTAiIHI9IjEwIiBmaWxsPSIjMTlmMmZmIiBvcGFjaXR5PSIwLjMiLz4KPHBhdGggZD0iTTEyNiAxMzUgUTE1MCAxNDggMTc0IDEzNSIgc3Ryb2tlPSIjNWMzZjIyIiBzdHJva2Utd2lkdGg9IjQiIGZpbGw9Im5vbmUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8cGF0aCBkPSJNMjE1IDcwIEMyNDUgNTAgMjcwIDc1IDI1OCAxMTAgQzI3OCA5NSAyODUgMTMwIDI1NSAxNTAgQzI3NSAxNjUgMjUwIDE5MCAyMTUgMTc1IEMyMzAgMTU1IDIyMCAxMzAgMjMwIDExMCBDMjE1IDEwMCAyMTAgODUgMjE1IDcwIFoiIGZpbGw9IiNiOTg3NWEiIHN0cm9rZT0iIzdhNWEzNyIgc3Ryb2tlLXdpZHRoPSIyIi8+CjxnPgo8cGF0aCBkPSJNMjMyIDU1IEwxNzUgMTUwIEwyMTIgMTUwIEwxODUgMjQ1IEwyNzIgMTMwIEwyMjggMTMwIFoiIGZpbGw9InVybCgjcmFpb0EpIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMi41Ii8+CjxwYXRoIGQ9Ik0yMzIgNTUgTDE3NSAxNTAgTDIxMiAxNTAgTDE4NSAyNDUgTDI3MiAxMzAgTDIyOCAxMzAgWiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmYmUwIiBzdHJva2Utd2lkdGg9IjEuMiIgb3BhY2l0eT0iMC44NSIvPgo8L2c+CjxwYXRoIGQ9Ik0yNDggOTAgTDIyNSAxMzAgTDI0MiAxMzAgTDIyOCAxNjUgTDI3MCAxMjAgTDI1MCAxMjAgWiIgZmlsbD0idXJsKCNyYWlvQikiIG9wYWNpdHk9IjAuOCIvPgo8Y2lyY2xlIGN4PSIyMzUiIGN5PSIxNDAiIHI9IjU1IiBmaWxsPSIjZmZmMGEwIiBvcGFjaXR5PSIwLjI1Ii8+CjxjaXJjbGUgY3g9IjIzNSIgY3k9IjE0MCIgcj0iOTAiIGZpbGw9IiNmZmYwYTAiIG9wYWNpdHk9IjAuMSIvPgo8L3N2Zz4K"
ZF_AVATAR_SUPREMO = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMDAgMzAwIj4KPGRlZnM+CjxyYWRpYWxHcmFkaWVudCBpZD0idGVtcGVzdGFkZSIgY3g9IjUwJSIgY3k9IjM1JSIgcj0iOTAlIj4KPHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iIzNiM2Y1NSIvPgo8c3RvcCBvZmZzZXQ9IjU1JSIgc3RvcC1jb2xvcj0iIzFiMWQyYiIvPgo8c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiMwNTA1MDgiLz4KPC9yYWRpYWxHcmFkaWVudD4KPGxpbmVhckdyYWRpZW50IGlkPSJyYWlvU3VwIiB4MT0iMCIgeTE9IjAiIHgyPSIxIiB5Mj0iMSI+CjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNmZmZmZmYiLz4KPHN0b3Agb2Zmc2V0PSI0MCUiIHN0b3AtY29sb3I9IiNlYWY2ZmYiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjN2ZkMWZmIi8+CjwvbGluZWFyR3JhZGllbnQ+CjxyYWRpYWxHcmFkaWVudCBpZD0ib2xob1Zlcm1lbGhvIiBjeD0iNTAlIiBjeT0iNTAlIiByPSI1MCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZmZkZWRlIi8+CjxzdG9wIG9mZnNldD0iNDAlIiBzdG9wLWNvbG9yPSIjZmYyZTJlIi8+CjxzdG9wIG9mZnNldD0iMTAwJSIgc3RvcC1jb2xvcj0iIzdhMDAwMCIvPgo8L3JhZGlhbEdyYWRpZW50Pgo8cmFkaWFsR3JhZGllbnQgaWQ9InBlbGUiIGN4PSI0MCUiIGN5PSIzMCUiIHI9IjcwJSI+CjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNjNzk0NzIiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjOGE1ZjNmIi8+CjwvcmFkaWFsR3JhZGllbnQ+CjwvZGVmcz4KPHJlY3Qgd2lkdGg9IjMwMCIgaGVpZ2h0PSIzMDAiIGZpbGw9InVybCgjdGVtcGVzdGFkZSkiLz4KPHBhdGggZD0iTTAgNjAgUTYwIDIwIDEyMCA1NSBRMTcwIDE1IDIzMCA1MCBRMjcwIDMwIDMwMCA2MCBMMzAwIDAgTDAgMCBaIiBmaWxsPSIjMmEyZDNkIiBvcGFjaXR5PSIwLjYiLz4KPHBhdGggZD0iTTE5MCA2MCBMMTYwIDEzMCBMMTg1IDEzMCBMMTUwIDIyMCBMMjQwIDExMCBMMjA1IDExMCBaIiBmaWxsPSJ1cmwoI3JhaW9TdXApIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMS41IiBvcGFjaXR5PSIwLjkiPgo8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJvcGFjaXR5IiB2YWx1ZXM9IjAuOTswLjQ7MC45IiBkdXI9IjEuNnMiIHJlcGVhdENvdW50PSJpbmRlZmluaXRlIi8+CjwvcGF0aD4KPHBhdGggZD0iTTYwIDE1MCBMMzUgMTk1IEw1MCAxOTUgTDI1IDI0NSBMOTAgMTg1IEw2OCAxODUgWiIgZmlsbD0idXJsKCNyYWlvU3VwKSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lkdGg9IjEuMiIgb3BhY2l0eT0iMC43Ij4KPGFuaW1hdGUgYXR0cmlidXRlTmFtZT0ib3BhY2l0eSIgdmFsdWVzPSIwLjc7MC4zOzAuNyIgZHVyPSIxLjFzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgo8L3BhdGg+CjxwYXRoIGQ9Ik0zMCAyNzAgQzU1IDIzMCA4MCAxOTAgMTIwIDE2MCBDMTQwIDE0MCAxNTUgMTIwIDE2NSAxMDAgTDE4NSAxMTUgQzE3NSAxMzUgMTYwIDE1NSAxNDAgMTc1IEMxMTAgMjA1IDgwIDIzNSA1NSAyNzAgWiIgZmlsbD0idXJsKCNwZWxlKSIgc3Ryb2tlPSIjNWM0MDI4IiBzdHJva2Utd2lkdGg9IjIiLz4KPHBhdGggZD0iTTkwIDExMCBRMTUwIDY1IDIxMCAxMTAgUTIxNSA3MCAxODAgNTAgUTE1MCAzMCAxMjAgNTAgUTkwIDcwIDkwIDExMCBaIiBmaWxsPSIjOWE5YWEwIi8+CjxwYXRoIGQ9Ik04MCA5NSBRNTUgMTMwIDY1IDE3NSBRNzUgMjEwIDk1IDIyNSBRNzUgMTkwIDc1IDE1MCBRNzUgMTE1IDgwIDk1IFoiIGZpbGw9IiM5YTlhYTAiLz4KPHBhdGggZD0iTTIyMCA5NSBRMjQ1IDEzMCAyMzUgMTc1IFEyMjUgMjEwIDIwNSAyMjUgUTIyNSAxOTAgMjI1IDE1MCBRMjI1IDExNSAyMjAgOTUgWiIgZmlsbD0iIzlhOWFhMCIvPgo8Y2lyY2xlIGN4PSIxNTAiIGN5PSIxMzUiIHI9IjQ4IiBmaWxsPSJ1cmwoI3BlbGUpIi8+CjxwYXRoIGQ9Ik0xMzUgMTE1IFExNDAgMTE4IDE0MiAxMjUiIHN0cm9rZT0iIzNhMmExYSIgc3Ryb2tlLXdpZHRoPSIzIiBmaWxsPSJub25lIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KPHBhdGggZD0iTTE2NSAxMTUgUTE2MCAxMTggMTU4IDEyNSIgc3Ryb2tlPSIjM2EyYTFhIiBzdHJva2Utd2lkdGg9IjMiIGZpbGw9Im5vbmUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8Y2lyY2xlIGN4PSIxMzgiIGN5PSIxMzAiIHI9IjgiIGZpbGw9InVybCgjb2xob1Zlcm1lbGhvKSI+CjxhbmltYXRlIGF0dHJpYnV0ZU5hbWU9InIiIHZhbHVlcz0iODs5LjU7OCIgZHVyPSIxLjRzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIvPgo8L2NpcmNsZT4KPGNpcmNsZSBjeD0iMTYyIiBjeT0iMTMwIiByPSI4IiBmaWxsPSJ1cmwoI29saG9WZXJtZWxobykiPgo8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJyIiB2YWx1ZXM9Ijg7OS41OzgiIGR1cj0iMS40cyIgcmVwZWF0Q291bnQ9ImluZGVmaW5pdGUiLz4KPC9jaXJjbGU+CjxwYXRoIGQ9Ik0xMTUgMTUwIFExNTAgMTcwIDE4NSAxNTAgUTE4MCAxOTUgMTUwIDIwNSBRMTIwIDE5NSAxMTUgMTUwIFoiIGZpbGw9IiM4YThhOTAiLz4KPHBhdGggZD0iTTExMCAxNzAgUTkwIDIyMCAxMTUgMjUwIFExMzUgMjY1IDE1MCAyNjUgUTE2NSAyNjUgMTg1IDI1MCBRMjEwIDIyMCAxOTAgMTcwIFExNzAgMTk1IDE1MCAyMDAgUTEzMCAxOTUgMTEwIDE3MCBaIiBmaWxsPSIjYjBiMGI2Ii8+CjxjaXJjbGUgY3g9IjE1MCIgY3k9IjE0MCIgcj0iNzAiIGZpbGw9IiNmZmVmYzAiIG9wYWNpdHk9IjAuMTIiPgo8YW5pbWF0ZSBhdHRyaWJ1dGVOYW1lPSJvcGFjaXR5IiB2YWx1ZXM9IjAuMDg7MC4yOzAuMDgiIGR1cj0iMnMiIHJlcGVhdENvdW50PSJpbmRlZmluaXRlIi8+CjwvY2lyY2xlPgo8L3N2Zz4K"

ZF_AVATAR_3D_FLAG = "__ZEUS_3D_MITOLOGICO__"

ZF_AVATARES = {
    "Zeus Classico (raio dourado, pose estatica)": ZF_AVATAR_CLASSICO,
    "Zeus em acao (lancando dois raios)": ZF_AVATAR_ACAO,
    "Zeus Supremo (olhos vermelhos, tempestade, animado)": ZF_AVATAR_SUPREMO,
    "Zeus Mitológico 3D (realista com raios nas mãos)": ZF_AVATAR_3D_FLAG,
}


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
    sp_mod = globals().get("spotipy")
    auth_cls = globals().get("SpotifyOAuth")
    if not sp_mod or not auth_cls:
        try:
            import spotipy as _sp_mod
            from spotipy.oauth2 import SpotifyOAuth as _auth_cls
            sp_mod, auth_cls = _sp_mod, _auth_cls
        except Exception:
            return "Biblioteca spotipy nao instalada."

    nome_musica = (nome_musica or "").strip()
    if not nome_musica:
        return "Nome da musica vazio."

    try:
        fn_sec = globals().get("_secret")
        def _ler_segredo(chave: str, padrao: str = "") -> str:
            if callable(fn_sec):
                try:
                    v = fn_sec(chave)
                    if v:
                        return str(v)
                except Exception:
                    pass
            try:
                if "st" in globals() and hasattr(st, "secrets") and chave in st.secrets:
                    return str(st.secrets[chave])
            except Exception:
                pass
            import os as _os_zf
            return _os_zf.environ.get(chave, padrao)

        client_id = _ler_segredo("SPOTIFY_CLIENT_ID")
        client_secret = _ler_segredo("SPOTIFY_CLIENT_SECRET")
        refresh_token = _ler_segredo("SPOTIFY_REFRESH_TOKEN")
        redirect_uri = _ler_segredo(
            "SPOTIFY_REDIRECT_URI",
            "https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/",
        )
        if not (client_id and client_secret and refresh_token):
            return "Faltam credenciais do Spotify nos Secrets."

        auth_manager = auth_cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-modify-playback-state user-read-playback-state",
            cache_path=None,
        )
        token_info = auth_manager.refresh_access_token(refresh_token)
        sp = sp_mod.Spotify(auth=token_info["access_token"])

        results = sp.search(q=nome_musica, limit=1, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            return f"Nenhuma faixa encontrada para {nome_musica}."

        track = tracks[0]
        dev_id = None
        try:
            if callable(globals().get("_zf_sp_device")):
                dev_id = globals()["_zf_sp_device"](sp)
            else:
                devs = sp.devices().get("devices", [])
                escolhido = st.session_state.get("zf2_dev_id") if "st" in globals() else None
                if escolhido and any(d.get("id") == escolhido for d in devs):
                    dev_id = escolhido
                else:
                    ativo = next((d.get("id") for d in devs if d.get("is_active")), None)
                    dev_id = ativo or (devs[0].get("id") if devs else None)
        except Exception:
            dev_id = None

        try:
            sp.start_playback(device_id=dev_id, uris=[track["uri"]])
            artista = track["artists"][0]["name"] if track.get("artists") else "desconhecido"
            return f"Tocando agora: {track['name']} - {artista}"
        except Exception as e:
            codigo = getattr(e, "http_status", None)
            if codigo == 404 or "NO_ACTIVE_DEVICE" in str(e):
                return "Nenhum aparelho ativo. Abra o Spotify e de play em algo primeiro."
            return f"Erro do Spotify: {str(e)[:150]}"
    except Exception as e:
        return f"Erro no Spotify: {str(e)[:150]}"
# ------------------------------------------------------------
# 5. INTERFACE (tela principal, nao escondida na sidebar)
# ------------------------------------------------------------
def zf_html_zeus_3d():
    """Avatar 3D realista de Zeus mitológico com raios nas mãos (Three.js)."""
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body, html { margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:#05070f; }
  #c { width:100%; height:100%; display:block; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<canvas id="c"></canvas>
<script>
const canvas = document.getElementById("c");
const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
camera.position.set(0, 1.4, 4.2);

scene.add(new THREE.AmbientLight(0x334466, 0.7));
const key = new THREE.DirectionalLight(0xfff0d0, 1.1);
key.position.set(3, 5, 4);
scene.add(key);
const rim = new THREE.PointLight(0x00ccff, 1.4, 12);
rim.position.set(-3, 2, 2);
scene.add(rim);
const glow = new THREE.PointLight(0xffaa00, 2.2, 8);
glow.position.set(0, 2.5, 0);
scene.add(glow);

const group = new THREE.Group();
scene.add(group);

// Corpo / torso
const torsoMat = new THREE.MeshStandardMaterial({color:0xc4a574, metalness:0.15, roughness:0.55});
const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.7, 1.3, 16), torsoMat);
torso.position.y = 0.9;
group.add(torso);

// Cabeça
const head = new THREE.Mesh(new THREE.SphereGeometry(0.42, 24, 24), torsoMat);
head.position.y = 1.85;
group.add(head);

// Cabelo / barba branca
const hairMat = new THREE.MeshStandardMaterial({color:0xf0f0f5, metalness:0.05, roughness:0.7});
const hair = new THREE.Mesh(new THREE.SphereGeometry(0.48, 16, 16), hairMat);
hair.position.y = 2.05;
hair.scale.y = 0.7;
group.add(hair);
const beard = new THREE.Mesh(new THREE.ConeGeometry(0.38, 0.55, 12), hairMat);
beard.position.set(0, 1.55, 0.28);
beard.rotation.x = Math.PI;
group.add(beard);

// Olhos brilhantes
const eyeMat = new THREE.MeshBasicMaterial({color:0x00eeff});
const le = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 12), eyeMat);
le.position.set(-0.14, 1.88, 0.36);
const re = le.clone(); re.position.x = 0.14;
group.add(le, re);

// Braços
const armMat = new THREE.MeshStandardMaterial({color:0xb8956a, metalness:0.1, roughness:0.6});
const armL = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 1.1, 10), armMat);
armL.position.set(-0.85, 1.15, 0.1);
armL.rotation.z = 0.55;
const armR = armL.clone();
armR.position.set(0.85, 1.15, 0.1);
armR.rotation.z = -0.55;
group.add(armL, armR);

// Raios (raios dourados nas mãos)
function makeBolt(x, y, z, rotZ) {
  const boltGroup = new THREE.Group();
  const boltMat = new THREE.MeshBasicMaterial({color:0xffee55, transparent:true, opacity:0.95});
  const core = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.02, 1.3, 6), boltMat);
  core.position.y = 0.65;
  boltGroup.add(core);
  // ramificações
  for (let i=0; i<4; i++) {
    const branch = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.01, 0.45, 5), boltMat);
    branch.position.set((i%2===0?0.12:-0.12), 0.4 + i*0.22, 0);
    branch.rotation.z = (i%2===0?0.6:-0.6);
    boltGroup.add(branch);
  }
  // glow
  const glowMesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.25, 12, 12),
    new THREE.MeshBasicMaterial({color:0xffcc00, transparent:true, opacity:0.25})
  );
  glowMesh.position.y = 0.3;
  boltGroup.add(glowMesh);

  boltGroup.position.set(x, y, z);
  boltGroup.rotation.z = rotZ;
  return boltGroup;
}
const boltL = makeBolt(-1.35, 0.7, 0.2, 0.4);
const boltR = makeBolt(1.35, 0.7, 0.2, -0.4);
group.add(boltL, boltR);

// Anel de energia
const ring = new THREE.Mesh(
  new THREE.TorusGeometry(1.6, 0.03, 12, 64),
  new THREE.MeshBasicMaterial({color:0x00ffff, transparent:true, opacity:0.6})
);
ring.rotation.x = Math.PI/2.4;
ring.position.y = 0.9;
group.add(ring);

function resize() {
  const w = canvas.clientWidth || 280;
  const h = canvas.clientHeight || 280;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

let t = 0;
function animate() {
  requestAnimationFrame(animate);
  t += 0.016;
  group.rotation.y = Math.sin(t * 0.4) * 0.25;
  boltL.rotation.z = 0.4 + Math.sin(t * 6) * 0.08;
  boltR.rotation.z = -0.4 + Math.sin(t * 6 + 1) * 0.08;
  ring.rotation.z += 0.012;
  glow.intensity = 1.8 + Math.sin(t * 5) * 0.6;
  renderer.render(scene, camera);
}
animate();
</script>
</body>
</html>
"""

def zf_render_painel():
    with st.container(border=True):
        col_av, col_cfg = st.columns([1, 2])
        with col_av:
            nome_avatar = st.session_state.get("zf_avatar_nome", list(ZF_AVATARES.keys())[-1])
            avatar_val = ZF_AVATARES.get(nome_avatar, ZF_AVATAR_SUPREMO)
            if avatar_val == ZF_AVATAR_3D_FLAG:
                import streamlit.components.v1 as components
                components.html(zf_html_zeus_3d(), height=280, width=280)
            else:
                st.image(avatar_val, width=160)
        with col_cfg:
            nome_avatar = st.selectbox("Preview do painel (não controla a tela principal)", list(ZF_AVATARES.keys()), key="zf_avatar_nome")
            st.session_state.zeus_avatar_ativo = ZF_AVATARES[nome_avatar]

            fn_vozes = globals().get("listar_todas_vozes_edge") or globals().get("listar_vozes_edge")
            vozes_disp = fn_vozes() if fn_vozes else globals().get("FALLBACK_VOZES", {"Francisca (pt-BR)": "pt-BR-FranciscaNeural"})

            filtro_voz = st.text_input("Filtrar vozes (ex: pt-BR, en-US)", key="zf_filtro_voz")
            vozes_filtradas = {k: v for k, v in vozes_disp.items() if filtro_voz.lower() in k.lower()} if filtro_voz else vozes_disp
            if not vozes_filtradas:
                vozes_filtradas = vozes_disp

            nome_voz = st.selectbox("Voz do Zeus", list(vozes_filtradas.keys()), key="zf_voz_nome")
            st.session_state.zf_voz_ativa = vozes_filtradas[nome_voz]

            if st.button("Ouvir previa", key="zf_btn_previa"):
                with st.spinner("Gerando previa..."):
                    audio_prev = zf_falar("Ola, Danilo. Eu sou Zeus.", vozes_filtradas[nome_voz])
                st.audio(audio_prev, format="audio/mp3") if audio_prev else st.error("Nao consegui gerar o audio.")

    with st.sidebar:
        st.divider()
        st.subheader("Spotify")

        def _zf_sp_cli():
            cid = st.secrets.get("SPOTIFY_CLIENT_ID")
            csec = st.secrets.get("SPOTIFY_CLIENT_SECRET")
            rtok = st.secrets.get("SPOTIFY_REFRESH_TOKEN")
            ruri = st.secrets.get("SPOTIFY_REDIRECT_URI", "https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/")
            if not (cid and csec and rtok):
                return None
            tok = SpotifyOAuth(
                client_id=cid,
                client_secret=csec,
                redirect_uri=ruri,
                scope="user-modify-playback-state user-read-playback-state",
                cache_path=None,
            ).refresh_access_token(rtok)
            return spotipy.Spotify(auth=tok["access_token"])

        sp_now = None
        pb_now = None
        try:
            sp_now = _zf_sp_cli()
            if sp_now:
                pb_now = sp_now.current_playback()
        except Exception:
            pb_now = None

        if pb_now and pb_now.get("item"):
            item = pb_now["item"]
            imgs = item.get("album", {}).get("images", [])
            if imgs:
                st.image(imgs[0]["url"], use_container_width=True)
            artistas = ", ".join(a.get("name", "") for a in item.get("artists", []))
            st.markdown(f"**{item.get('name', '')}**")
            if artistas:
                st.caption(artistas)

        musica_input = st.text_input("Musica ou artista:", key="zf_input_musica")

        if hasattr(st, "audio_input"):
            audio_voz_sp = st.audio_input("Pedir por voz", key="zf_sp_voz_mic")
            if audio_voz_sp is not None:
                audio_bytes = audio_voz_sp.getvalue()
                audio_hash = hash(audio_bytes)
                if st.session_state.get("zf_sp_last_mic_hash") != audio_hash:
                    st.session_state.zf_sp_last_mic_hash = audio_hash
                    with st.spinner("Ouvindo pedido..."):
                        try:
                            from openai import OpenAI as _OpenAI_SP
                            _oai_key = st.secrets.get("OPENAI_API_KEY")
                            if _oai_key:
                                _cli_oai = _OpenAI_SP(api_key=_oai_key)
                                _tr = _cli_oai.audio.transcriptions.create(
                                    model="whisper-1",
                                    file=("pedido.wav", audio_bytes, "audio/wav"),
                                    language="pt",
                                )
                                _cmd_txt = (_tr.text or "").strip()
                                _termo_voz = _re_zf.sub(
                                    r"\b(zeus|por favor|toca|tocar|toque|a musica|musica|no spotify|spotify)\b",
                                    "",
                                    _cmd_txt.lower(),
                                ).strip(" .,!?:-")
                                if _termo_voz:
                                    res_voz = zf_tocar_musica(_termo_voz)
                                    st.toast(res_voz, icon="🎵")
                                    st.rerun()
                                else:
                                    st.toast("Nao entendi o nome da musica.", icon="⚠️")
                            else:
                                st.toast("Falta OPENAI_API_KEY nos Secrets.", icon="⚠️")
                        except Exception as _e_mic:
                            st.toast(f"Erro na voz: {str(_e_mic)[:90]}", icon="⚠️")

        c_sp1, c_sp2, c_sp3, c_sp4 = st.columns(4)
        with c_sp1:
            if st.button("▶", key="zf_btn_tocar", use_container_width=True, help="Tocar musica digitada"):
                if musica_input.strip():
                    with st.spinner("Buscando..."):
                        res_sp = zf_tocar_musica(musica_input.strip())
                    st.toast(res_sp, icon="🎵")
                    st.rerun()
                else:
                    st.toast("Digite a musica.", icon="⚠️")
        with c_sp2:
            if st.button("⏮", key="zf_btn_sp_prev", use_container_width=True, help="Anterior"):
                try:
                    cli = sp_now or _zf_sp_cli()
                    if cli:
                        cli.previous_track()
                        st.rerun()
                except Exception as e:
                    st.toast(str(e)[:90], icon="⚠️")
        with c_sp3:
            if st.button("⏯", key="zf_btn_sp_pause", use_container_width=True, help="Play / Pausa"):
                try:
                    cli = sp_now or _zf_sp_cli()
                    if cli:
                        pb = pb_now or cli.current_playback()
                        if pb and pb.get("is_playing"):
                            cli.pause_playback()
                        else:
                            cli.start_playback()
                        st.rerun()
                except Exception as e:
                    st.toast(str(e)[:90], icon="⚠️")
        with c_sp4:
            if st.button("⏭", key="zf_btn_sp_next", use_container_width=True, help="Proxima"):
                try:
                    cli = sp_now or _zf_sp_cli()
                    if cli:
                        cli.next_track()
                        st.rerun()
                except Exception as e:
                    st.toast(str(e)[:90], icon="⚠️")

        c_vol1, c_vol2, c_vol3 = st.columns([1, 1, 1])
        vol_atual = 50
        if pb_now and isinstance(pb_now.get("device"), dict) and pb_now["device"].get("volume_percent") is not None:
            vol_atual = int(pb_now["device"]["volume_percent"])
        st.session_state.setdefault("zf_sp_vol", vol_atual)
        with c_vol1:
            if st.button("🔉 -", key="zf_btn_vol_menos", use_container_width=True):
                try:
                    cli = sp_now or _zf_sp_cli()
                    if cli:
                        novo_vol = max(0, int(st.session_state.get("zf_sp_vol", vol_atual)) - 10)
                        cli.volume(novo_vol)
                        st.session_state.zf_sp_vol = novo_vol
                        st.toast(f"Volume: {novo_vol}%", icon="🔉")
                        st.rerun()
                except Exception as e:
                    st.toast(str(e)[:90], icon="⚠️")
        with c_vol2:
            st.markdown(f"<div style='text-align:center;padding-top:6px;font-size:13px;'>{st.session_state.get('zf_sp_vol', vol_atual)}%</div>", unsafe_allow_html=True)
        with c_vol3:
            if st.button("🔊 +", key="zf_btn_vol_mais", use_container_width=True):
                try:
                    cli = sp_now or _zf_sp_cli()
                    if cli:
                        novo_vol = min(100, int(st.session_state.get("zf_sp_vol", vol_atual)) + 10)
                        cli.volume(novo_vol)
                        st.session_state.zf_sp_vol = novo_vol
                        st.toast(f"Volume: {novo_vol}%", icon="🔊")
                        st.rerun()
                except Exception as e:
                    st.toast(str(e)[:90], icon="⚠️")

    with st.expander("Zeus - Nucleo de Aprendizado, Database (zilsy/) e Midia", expanded=False):
        aba_criar, aba_memoria, aba_midia = st.tabs(["Aprender (Ensemble)", "Memoria GitHub", "Midia Aprimorada"])

        with aba_criar:
            instrucao = st.text_input("O que o Zeus deve desenvolver?",
                                      placeholder="Ex: funcao que calcula sensacao termica...", key="zf_instrucao")
            linguagem = st.selectbox("Linguagem", ["python", "javascript", "cpp", "java"], key="zf_linguagem")
            st.caption("Python executa automaticamente. Outras linguagens sao geradas e salvas, sem execucao automatica.")
            if st.button("Gerar, Revisar e Salvar", key="zf_btn_aprender"):
                if instrucao.strip():
                    with st.spinner("Gerando, revisando e salvando..."):
                        resultado = zf_aprender_ensemble(instrucao.strip(), linguagem)
                    st.markdown(resultado)
                else:
                    st.warning("Insira uma instrucao valida.")

        with aba_memoria:
            pasta_consulta = st.text_input("Pasta no Database (GitHub):", value=_ZF_PASTA_MODULOS, key="zf_pasta_consulta")
            if st.button("Listar Arquivos", key="zf_btn_listar"):
                pasta_segura = _zf_sanitizar_pasta(pasta_consulta)
                arquivos = zf_listar_memoria_github(pasta_segura)
                st.session_state.zf_arquivos = arquivos
                st.success(f"Encontrados {len(arquivos)} arquivos.") if arquivos else st.warning("Nenhum arquivo encontrado.")
            if st.session_state.get("zf_arquivos"):
                arq_sel = st.selectbox("Selecione um modulo:", st.session_state.zf_arquivos, key="zf_arq_sel")
                if st.button("Ler Conteudo", key="zf_btn_ler"):
                    conteudo = zf_ler_arquivo_github(arq_sel)
                    st.code(conteudo, language="python") if conteudo else st.error("Erro ao recuperar conteudo.")

        with aba_midia:
            prompt_midia = st.text_area("Descreva a imagem/video:", key="zf_prompt_midia")
            tipo_midia = st.radio("Tipo:", ["Imagem aprimorada", "Video aprimorado"], key="zf_tipo_midia", horizontal=True)
            if st.button("Gerar Midia Aprimorada", key="zf_btn_midia"):
                if prompt_midia.strip():
                    log_midia = []
                    with st.spinner("Gerando e aprimorando..."):
                        if tipo_midia == "Imagem aprimorada":
                            img = zf_gerar_imagem_aprimorada(prompt_midia.strip(), log_midia)
                            if img:
                                st.image(img, caption="Imagem aprimorada")
                                st.download_button("Baixar", img, file_name="zeus_imagem.png", key="zf_dl_img")
                            else:
                                st.error("Nao consegui gerar a imagem.")
                        else:
                            vid, img_base = zf_gerar_video_aprimorado(prompt_midia.strip(), log_midia)
                            if img_base:
                                st.image(img_base, caption="Quadro base aprimorado")
                            if vid:
                                st.video(vid)
                                st.download_button("Baixar video", vid, file_name="zeus_video.mp4", key="zf_dl_vid")
                            else:
                                st.error("Nao consegui gerar o video.")
                    with st.expander("Log de geracao", expanded=False):
                        st.write(chr(10).join(log_midia))
                else:
                    st.warning("Descreva o que deseja gerar.")


def zf_interceptar_musica():
    msgs = st.session_state.get("messages")
    if not msgs:
        return
    ultima = msgs[-1]
    if ultima.get("role") != "user":
        return
    texto = str(ultima.get("content", "")).lower()
    if not any(p in texto for p in ("toca ", "tocar ", "toque ", "spotify", "musica ")):
        return
    if st.session_state.get("zf_ultima_musica") == ultima["content"]:
        return

    termo = _re_zf.sub(
        r"\b(zeus|por favor|toca|tocar|toque|a musica|musica|no spotify|spotify)\b", "", texto
    ).strip(" .,!?:-")
    st.session_state.zf_ultima_musica = ultima["content"]
    if not termo:
        return

    with st.spinner(f"Buscando {termo}..."):
        resultado = zf_tocar_musica(termo)
    st.toast(resultado, icon="🎵")
    st.session_state.messages.append({"role": "assistant", "content": resultado})
    st.rerun()


try:
    zf_render_painel()
    zf_interceptar_musica()
except Exception as _zf_err:
    st.caption("Zeus Final desativado por erro: " + str(_zf_err)[:200])



# ============================================================
# ROTEADOR DE COMANDOS AUTÔNOMOS NO CHAT
# Reconhece intenções de ação na mensagem do usuário e executa
# a função correspondente em vez de só responder em texto.
# Cada ação só roda porque você escreveu aquela mensagem -
# nada é agendado, nada roda sozinho em segundo plano.
# ============================================================

# ============================================================
# TELEGRAM + E-MAIL (comandos pelo chat)
# Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#          SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM
# ============================================================
def zf_enviar_telegram(texto: str, chat_id_destino: str = "") -> str:
    token = _secret("TELEGRAM_BOT_TOKEN")
    chat_id_padrao = _secret("TELEGRAM_CHAT_ID")
    chat_id = str(chat_id_destino or chat_id_padrao or "").strip()
    if not token or not chat_id:
        return "Telegram não configurado. Coloque TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets."
    texto = (texto or "").strip()
    if not texto:
        return "Mensagem vazia. Diga o que devo enviar no Telegram."
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto[:4000]},
            timeout=30,
        )
        if r.status_code == 200 and r.json().get("ok"):
            return f"✅ Mensagem enviada no Telegram."
        return f"❌ Falha no Telegram ({r.status_code}): {r.text[:180]}"
    except Exception as e:
        return f"❌ Erro Telegram: {str(e)[:150]}"


def zf_enviar_email(para: str, assunto: str, corpo: str) -> str:
    import smtplib
    from email.mime.text import MIMEText
    host = _secret("SMTP_HOST") or "smtp.gmail.com"
    port = int(_secret("SMTP_PORT") or "587")
    user = _secret("SMTP_USER")
    password = _secret("SMTP_PASS")
    remetente = _secret("EMAIL_FROM") or user
    if not user or not password:
        return "E-mail não configurado. Coloque SMTP_USER, SMTP_PASS (e opcionalmente SMTP_HOST, SMTP_PORT, EMAIL_FROM) nos Secrets."
    para = (para or "").strip()
    if not para or "@" not in para:
        return "Destinatário inválido. Use: envia email para alguém@dominio.com assunto ... corpo ..."
    try:
        msg = MIMEText(corpo or "", "plain", "utf-8")
        msg["Subject"] = assunto or "Mensagem do Zeus"
        msg["From"] = remetente
        msg["To"] = para
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(remetente, [para], msg.as_string())
        return f"✅ E-mail enviado para {para}."
    except Exception as e:
        return f"❌ Erro ao enviar e-mail: {str(e)[:180]}"


def _rt_comando_telegram(texto):
    # Exemplos: "manda no telegram oi danilo", "telegram: reunião às 15h", "envia no telegram ..."
    t = re.sub(
        r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*",
        "",
        texto,
    ).strip(" .,!?:-")
    if not t:
        return "O que devo enviar no Telegram? Ex: manda no telegram Reunião confirmada às 15h"
    return zf_enviar_telegram(t)


def _rt_comando_email(texto):
    # Formato preferido: envia email para x@y.com assunto ASSUNTO corpo CORPO
    low = texto.lower()
    para = assunto = corpo = ""
    m_para = re.search(r"(?i)(?:para|to)\s+([^\s]+@[^\s]+)", texto)
    if m_para:
        para = m_para.group(1).strip(" .,;:")
    m_ass = re.search(r"(?i)assunto\s+(.+?)(?:\s+corpo\s+|$)", texto)
    if m_ass:
        assunto = m_ass.group(1).strip(" .,;:")
    m_corpo = re.search(r"(?i)corpo\s+(.+)$", texto)
    if m_corpo:
        corpo = m_corpo.group(1).strip()
    if not corpo and not m_ass:
        # fallback: tudo depois de "email" como corpo, se tiver email
        resto = re.sub(r"(?i).*?\b(e-?mail|email)\b\s*(para\s+[^\s]+@[^\s]+\s*)?", "", texto).strip()
        if para and resto:
            corpo = resto
        elif not para:
            return (
                "Formato: envia email para alguem@dominio.com assunto Título corpo Texto da mensagem"
            )
    if not corpo:
        corpo = assunto or "Mensagem enviada pelo Zeus."
        if not assunto:
            assunto = "Mensagem do Zeus"
    if not assunto:
        assunto = "Mensagem do Zeus"
    return zf_enviar_email(para, assunto, corpo)


_processar_prompt_original = processar_prompt


def _rt_extrair_pedido(texto, remover):
    t = texto
    for termo in remover:
        t = re.sub(termo, " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" .,!?:-")


def _rt_comando_criar_habilidade(texto):
    pedido = _rt_extrair_pedido(texto, [r"\bzeus\b", r"\bcria(r)?\b", r"\bfaça\b", r"\bfaca\b",
                                        r"\buma?\b", r"\bhabilidade\b", r"\bfun[cç][aã]o\b", r"\bscript\b", r"\bpor favor\b"])
    if not pedido:
        return "Me diga o que a habilidade deve fazer. Ex: 'Zeus, cria uma habilidade que calcula o IMC'."
    fn = globals().get("zf_aprender_ensemble")
    if not fn:
        return "❌ Módulo de aprendizado (zf_aprender_ensemble) não está carregado neste app."
    resultado = fn(pedido, "python")
    st.session_state["rt_ultimo_codigo_gerado"] = {"pedido": pedido}
    return "⚡ Comando recebido: criar habilidade.\n\n" + resultado


def _rt_comando_criar_imagem(texto):
    pedido = _rt_extrair_pedido(texto, [r"\bzeus\b", r"\bcria(r)?\b", r"\bgera(r)?\b", r"\bum(a)?\b",
                                        r"\bimagem\b", r"\bavatar\b", r"\bfoto\b", r"\bilustra[cç][aã]o\b", r"\bpor favor\b"])
    if not pedido:
        return "Descreva a imagem que devo gerar. Ex: 'Zeus, cria uma imagem de um dragão de cristal'."
    fn = globals().get("zf_gerar_imagem_aprimorada") or globals().get("make_image")
    if not fn:
        return "❌ Módulo de geração de imagem não está carregado neste app."
    log_midia = []
    img = fn(pedido, log_midia)
    if img:
        st.session_state["rt_imagem_gerada"] = img
        return f'🎨 Imagem gerada a partir de: "{pedido}". Ela aparece logo abaixo do painel principal.'
    return "❌ Não consegui gerar a imagem agora. Log: " + " | ".join(log_midia[-3:])


def _rt_comando_publicar_github(texto):
    pendente = st.session_state.get("rt_ultimo_codigo_gerado")
    if not pendente:
        return "Não há nenhuma habilidade gerada recentemente nesta sessão para publicar."
    return "✅ A última habilidade já foi salva automaticamente no GitHub durante a criação (veja o link na resposta anterior)."


_RT_REGRAS = [
    (r"\b(cria|criar|faça|faca)\b.*\b(habilidade|fun[cç][aã]o|script)\b", _rt_comando_criar_habilidade),
    (r"\b(cria|criar|gera|gerar)\b.*\b(imagem|avatar|foto|ilustra[cç][aã]o)\b", _rt_comando_criar_imagem),
    (r"\b(salva|publica|comita)\b.*\bgithub\b", _rt_comando_publicar_github),
    (r"\b(telegram|tg)\b", _rt_comando_telegram),
    (r"\b(e-?mail|email)\b", _rt_comando_email),
]


def interceptar_comando_autonomo(texto):
    """Retorna (tratado: bool, resposta: str)."""
    for padrao, fn in _RT_REGRAS:
        if re.search(padrao, texto, re.I):
            return True, fn(texto)
    return False, ""


def processar_prompt(prompt):
    """Substitui a versão original: primeiro verifica se é um comando de
    ação reconhecido; se não for, cai no comportamento normal de chat."""
    tratado, resposta = interceptar_comando_autonomo(prompt)
    if tratado:
        sid = st.session_state.session_id
        st.session_state.messages.append({"role": "user", "content": prompt})
        add_message(sid, "user", prompt)
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        add_message(sid, "assistant", resposta)
        st.session_state.last_info = "⚡ Comando autônomo executado"
        return resposta
    return _processar_prompt_original(prompt)


try:
    _rt_img_pendente = st.session_state.get("rt_imagem_gerada")
    if _rt_img_pendente:
        st.markdown("### 🎨 Última imagem gerada por comando")
        st.image(_rt_img_pendente, caption="Gerada via comando no chat")
except Exception as _rt_err:
    st.caption("⚠️ Roteador de comandos desativado por erro: " + str(_rt_err)[:200])

# ============================================================================
# ZEUS :: MÓDULO DE AUTONOMIA, MEMÓRIA E REFLEXÃO COGNITIVA (ZEUSTRACK)
# Arquivo base em pasta app/app.py
# ============================================================================
import json as _zjson
import os as _zos
import re as _zre
import sqlite3 as _zsql
import threading as _zthr
import unicodedata as _zuni
from datetime import datetime as _zdt
from difflib import SequenceMatcher as _zSM

try:
    from dotenv import load_dotenv as _zload_dotenv
    _zload_dotenv()
except ImportError:
    pass


def _zeus_env_str(nome: str, padrao: str = "") -> str:
    val = _zos.getenv(nome)
    return val.strip() if val and val.strip() else padrao


try:
    _ZEUS_DIR = _zos.path.dirname(_zos.path.abspath(__file__))
except NameError:
    _ZEUS_DIR = _zos.getcwd()

_default_db = _zos.path.join(_ZEUS_DIR, "zeus_memoria.db")
ZEUS_DB_PATH = _zos.path.abspath(_zeus_env_str("ZEUS_DB_PATH", _default_db))
_zos.makedirs(_zos.path.dirname(ZEUS_DB_PATH), exist_ok=True)
_ZEUS_LOCK = _zthr.Lock()

# ----------------------------------------------------------------------------
# Diretrizes soberanas (editáveis em runtime via zeus_definir_diretriz)
# ----------------------------------------------------------------------------
_ZEUS_DIRETRIZES_PADRAO = {
    "identidade": "Zeus: assistente soberano, técnico, direto e objetivo.",
    "limiar_memoria_local": 0.82,   # similaridade mínima p/ responder só com memória
    "limiar_correcao": 0.70,        # similaridade mínima p/ aplicar correção passada
    "confianca_minima": 0.55,       # confiança mínima de um conhecimento p/ uso local
    "max_chars_resposta": 4000,
    "max_exemplos_contexto": 3,
    "frases_removidas": [
        r"como (um|uma) (modelo de linguagem|ia|intelig[eê]ncia artificial)[^.\n]*[.!\n]\s*",
        r"as an ai( language model)?[^.\n]*[.!\n]\s*",
        r"sou apenas (uma|um) (ia|assistente virtual)[^.\n]*[.!\n]\s*",
        r"espero (ter )?ajudado[^.\n]*[.!]?\s*",
        r"(great|good) question[!.]?\s*",
        r"(com certeza|claro|certamente|perfeito)[!,.]\s+",
        r"n[aã]o hesite em (me )?(perguntar|contatar)[^.\n]*[.!]?\s*",
        r"de acordo com (as )?minhas diretrizes[^.\n]*[.,]?\s*",
    ],
}

_ZEUS_STOP = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma",
    "para", "por", "com", "que", "eh", "me", "se", "no", "na", "nos", "nas",
    "the", "is", "of", "to", "and"
}


def _zeus_conn():
    c = _zsql.connect(ZEUS_DB_PATH, timeout=10, check_same_thread=False)
    c.row_factory = _zsql.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c


def zeus_init_memoria():
    """Cria as tabelas (idempotente) e grava diretrizes padrão ausentes."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS interacoes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, pergunta TEXT,
            pergunta_norm TEXT, resposta TEXT, origem TEXT,
            feedback INTEGER, correcao TEXT);
        CREATE TABLE IF NOT EXISTS conhecimento(
            id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_norm TEXT UNIQUE,
            pergunta TEXT, resposta TEXT, confianca REAL DEFAULT 0.5,
            usos INTEGER DEFAULT 0, acertos INTEGER DEFAULT 0, erros INTEGER DEFAULT 0,
            origem TEXT, atualizado TEXT);
        CREATE TABLE IF NOT EXISTS diretrizes(chave TEXT PRIMARY KEY, valor TEXT);
        """)
        for k, v in _ZEUS_DIRETRIZES_PADRAO.items():
            c.execute("INSERT OR IGNORE INTO diretrizes VALUES(?,?)", (k, _zjson.dumps(v)))


def zeus_diretriz(chave, padrao=None):
    try:
        with _zeus_conn() as c:
            r = c.execute("SELECT valor FROM diretrizes WHERE chave=?", (chave,)).fetchone()
        return _zjson.loads(r["valor"]) if r else _ZEUS_DIRETRIZES_PADRAO.get(chave, padrao)
    except Exception:
        return _ZEUS_DIRETRIZES_PADRAO.get(chave, padrao)


def zeus_definir_diretriz(chave, valor):
    """Altera/cria uma diretriz soberana persistente."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.execute("INSERT OR REPLACE INTO diretrizes VALUES(?,?)", (chave, _zjson.dumps(valor)))


# ----------------------------------------------------------------------------
# Similaridade (sem dependências externas)
# ----------------------------------------------------------------------------
def _zeus_norm(txt):
    t = _zuni.normalize("NFKD", str(txt or "").lower())
    t = "".join(ch for ch in t if not _zuni.combining(ch))
    t = _zre.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(w for w in t.split() if w not in _ZEUS_STOP)


def _zeus_sim(a, b):
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    seq = _zSM(None, a, b).ratio()
    # Perguntas curtas têm maior peso lexical/sequencial para evitar falsos positivos
    if min(len(ta), len(tb)) <= 2:
        return round(0.35 * jac + 0.65 * seq, 4)
    return round(0.5 * jac + 0.5 * seq, 4)


# ----------------------------------------------------------------------------
# 1) Memória de experiência
# ----------------------------------------------------------------------------
def zeus_registrar_interacao(pergunta, resposta, origem="api"):
    """Registra uma interação e devolve seu id (use p/ enviar feedback depois)."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        cur = c.execute(
            "INSERT INTO interacoes(ts,pergunta,pergunta_norm,resposta,origem) VALUES(?,?,?,?,?)",
            (_zdt.now().isoformat(timespec="seconds"), pergunta, _zeus_norm(pergunta), resposta, origem),
        )
        return cur.lastrowid


def zeus_registrar_feedback(interacao_id, acertou, correcao=None):
    """
    Aprende com o feedback.
    acertou=True  -> promove a resposta a conhecimento e sobe a confiança.
    acertou=False -> penaliza; se vier `correcao`, ela vira o conhecimento prioritário.
    """
    with _ZEUS_LOCK, _zeus_conn() as c:
        it = c.execute("SELECT * FROM interacoes WHERE id=?", (interacao_id,)).fetchone()
        if not it:
            return False
        c.execute(
            "UPDATE interacoes SET feedback=?, correcao=? WHERE id=?",
            (1 if acertou else 0, correcao, interacao_id),
        )
        agora = _zdt.now().isoformat(timespec="seconds")
        chave = it["pergunta_norm"]
        if not chave:
            return True
        if acertou:
            c.execute(
                """INSERT INTO conhecimento(pergunta_norm,pergunta,resposta,confianca,acertos,origem,atualizado)
                   VALUES(?,?,?,0.65,1,'validado',?)
                   ON CONFLICT(pergunta_norm) DO UPDATE SET
                     acertos=acertos+1,
                     confianca=MIN(1.0, confianca+0.1),
                     atualizado=excluded.atualizado""",
                (chave, it["pergunta"], it["resposta"], agora),
            )
        elif correcao and correcao.strip():
            c.execute(
                """INSERT INTO conhecimento(pergunta_norm,pergunta,resposta,confianca,erros,origem,atualizado)
                   VALUES(?,?,?,0.95,0,'correcao',?)
                   ON CONFLICT(pergunta_norm) DO UPDATE SET
                     resposta=excluded.resposta, confianca=0.95,
                     origem='correcao', atualizado=excluded.atualizado""",
                (chave, it["pergunta"], correcao.strip(), agora),
            )
        else:
            c.execute(
                """UPDATE conhecimento SET erros=erros+1,
                   confianca=MAX(0.0, confianca-0.25), atualizado=? WHERE pergunta_norm=?""",
                (agora, chave),
            )
        return True


def zeus_buscar_conhecimento(pergunta, limiar=None):
    """Melhor conhecimento local para a pergunta: (registro|None, similaridade)."""
    limiar = float(zeus_diretriz("limiar_correcao", 0.70)) if limiar is None else float(limiar)
    q = _zeus_norm(pergunta)
    melhor, melhor_s = None, 0.0
    with _ZEUS_LOCK, _zeus_conn() as c:
        for r in c.execute("SELECT * FROM conhecimento WHERE confianca>0"):
            base_sim = _zeus_sim(q, r["pergunta_norm"])
            s = base_sim * (0.7 + 0.3 * float(r["confianca"]))
            if r["origem"] == "correcao":
                s = min(1.0, s * 1.05)
            if s > melhor_s:
                melhor, melhor_s = r, s
    return (dict(melhor), round(melhor_s, 4)) if melhor is not None and melhor_s >= limiar else (None, round(melhor_s, 4))


def zeus_contexto_para_prompt(pergunta):
    """Texto com diretrizes + experiências passadas para injetar no prompt enviado à API."""
    n = int(zeus_diretriz("max_exemplos_contexto", 3))
    q = _zeus_norm(pergunta)
    with _ZEUS_LOCK, _zeus_conn() as c:
        regs = [dict(r) for r in c.execute("SELECT * FROM conhecimento WHERE confianca>=0.5")]
    regs.sort(
        key=lambda r: _zeus_sim(q, r["pergunta_norm"]) + (0.2 if r["origem"] == "correcao" else 0),
        reverse=True,
    )
    ex = [r for r in regs[:n] if _zeus_sim(q, r["pergunta_norm"]) > 0.3]
    txt = f"[IDENTIDADE] {zeus_diretriz('identidade')}\n"
    if ex:
        txt += "[EXPERIÊNCIA VALIDADA - priorize isto sobre conhecimento genérico]\n"
        for r in ex:
            txt += f"- P: {r['pergunta']}\n  R: {r['resposta']}\n"
    return txt


# ----------------------------------------------------------------------------
# 2) Ciclo de reflexão crítica
# ----------------------------------------------------------------------------
def zeus_avaliar_parametros(pergunta, resposta):
    """Pontua a resposta segundo as diretrizes. Retorna dict com nota (0-1) e alertas."""
    alertas, nota = [], 1.0
    texto = resposta or ""
    if not texto.strip():
        return {"nota": 0.0, "alertas": ["resposta vazia"]}
    for pad in zeus_diretriz("frases_removidas", []):
        if _zre.search(pad, texto, _zre.I):
            alertas.append("tom genérico/corporativo detectado")
            nota -= 0.15
            break
    if len(texto) > int(zeus_diretriz("max_chars_resposta", 4000)):
        alertas.append("resposta acima do limite")
        nota -= 0.1
    conhecido, _ = zeus_buscar_conhecimento(pergunta, 0.80)
    if (
        conhecido
        and conhecido["origem"] == "correcao"
        and _zeus_sim(_zeus_norm(texto), _zeus_norm(conhecido["resposta"])) < 0.35
    ):
        alertas.append("diverge de correção aprendida")
        nota -= 0.3
    return {"nota": max(0.0, round(nota, 2)), "alertas": alertas}


def zeus_refletir(resposta, pergunta=""):
    """
    Filtro final antes de entregar: remove boilerplate corporativo, normaliza
    o tom técnico/direto e aplica o limite de tamanho. Retorna (texto, relatório).
    """
    original = resposta or ""
    texto = original
    for pad in zeus_diretriz("frases_removidas", []):
        texto = _zre.sub(pad, "", texto, flags=_zre.I)
    texto = _zre.sub(r"[ \t]+\n", "\n", texto)
    texto = _zre.sub(r"\n{3,}", "\n\n", texto).strip()
    if texto and texto[0].islower():
        texto = texto[0].upper() + texto[1:]
    limite = int(zeus_diretriz("max_chars_resposta", 4000))
    if len(texto) > limite:
        texto = texto[:limite].rsplit(" ", 1)[0] + "…"
    if not texto:
        texto = original.strip()
    rel = zeus_avaliar_parametros(pergunta, texto)
    rel["alterada"] = texto != original.strip()
    return texto, rel


# ============================================================================
# ZEUS :: MÓDULO DE AUTONOMIA, MEMÓRIA E REFLEXÃO COGNITIVA
# Cole este bloco no FINAL do app.py. Não renderiza nada sozinho (sem UI),
# só define funções. Nenhuma dependência além da biblioteca padrão.
# ============================================================================
import os as _zos
import re as _zre
import json as _zjson
import sqlite3 as _zsql
import threading as _zthr
import unicodedata as _zuni
from datetime import datetime as _zdt
from difflib import SequenceMatcher as _zSM

try:
    _ZEUS_DIR = _zos.path.dirname(_zos.path.abspath(__file__))
except NameError:
    _ZEUS_DIR = _zos.getcwd()
ZEUS_DB_PATH = _zos.path.join(_ZEUS_DIR, "zeus_memoria.db")
_ZEUS_LOCK = _zthr.Lock()

# ----------------------------------------------------------------------------
# Diretrizes soberanas (editáveis em runtime via zeus_definir_diretriz)
# ----------------------------------------------------------------------------
_ZEUS_DIRETRIZES_PADRAO = {
    "identidade": "Zeus: assistente soberano, técnico, direto e objetivo.",
    "limiar_memoria_local": 0.82,   # similaridade mínima p/ responder só com memória
    "limiar_correcao": 0.70,        # similaridade mínima p/ aplicar correção passada
    "confianca_minima": 0.55,       # confiança mínima de um conhecimento p/ uso local
    "max_chars_resposta": 4000,
    "max_exemplos_contexto": 3,
    "frases_removidas": [
        r"como (um|uma) (modelo de linguagem|ia|intelig[eê]ncia artificial)[^.\n]*[.\n]\s*",
        r"as an ai( language model)?[^.\n]*[.\n]\s*",
        r"sou apenas (uma|um) (ia|assistente virtual)[^.\n]*[.\n]\s*",
        r"espero (ter )?ajudado[^.\n]*[.!]?\s*",
        r"(great|good) question[!.]?\s*",
        r"(com certeza|claro|certamente|perfeito)[!,.]\s+",
        r"n[aã]o hesite em (me )?(perguntar|contatar)[^.\n]*[.!]?\s*",
        r"de acordo com (as )?minhas diretrizes[^.\n]*[.,]?\s*",
    ],
}

_ZEUS_STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma",
              "para", "por", "com", "que", "e", "eh", "me", "se", "no", "na", "nos", "nas",
              "the", "is", "of", "to", "and"}


def _zeus_conn():
    c = _zsql.connect(ZEUS_DB_PATH, timeout=10, check_same_thread=False)
    c.row_factory = _zsql.Row
    return c


def zeus_init_memoria():
    """Cria as tabelas (idempotente) e grava diretrizes padrão ausentes."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS interacoes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, pergunta TEXT,
            pergunta_norm TEXT, resposta TEXT, origem TEXT,
            feedback INTEGER, correcao TEXT);
        CREATE TABLE IF NOT EXISTS conhecimento(
            id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_norm TEXT UNIQUE,
            pergunta TEXT, resposta TEXT, confianca REAL DEFAULT 0.5,
            usos INTEGER DEFAULT 0, acertos INTEGER DEFAULT 0, erros INTEGER DEFAULT 0,
            origem TEXT, atualizado TEXT);
        CREATE TABLE IF NOT EXISTS diretrizes(chave TEXT PRIMARY KEY, valor TEXT);
        """)
        for k, v in _ZEUS_DIRETRIZES_PADRAO.items():
            c.execute("INSERT OR IGNORE INTO diretrizes VALUES(?,?)", (k, _zjson.dumps(v)))


def zeus_diretriz(chave, padrao=None):
    try:
        with _zeus_conn() as c:
            r = c.execute("SELECT valor FROM diretrizes WHERE chave=?", (chave,)).fetchone()
        return _zjson.loads(r["valor"]) if r else _ZEUS_DIRETRIZES_PADRAO.get(chave, padrao)
    except Exception:
        return _ZEUS_DIRETRIZES_PADRAO.get(chave, padrao)


def zeus_definir_diretriz(chave, valor):
    """Altera/cria uma diretriz soberana persistente."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.execute("INSERT OR REPLACE INTO diretrizes VALUES(?,?)", (chave, _zjson.dumps(valor)))


# ----------------------------------------------------------------------------
# Similaridade (sem dependências externas)
# ----------------------------------------------------------------------------
def _zeus_norm(txt):
    t = _zuni.normalize("NFKD", str(txt or "").lower())
    t = "".join(ch for ch in t if not _zuni.combining(ch))
    t = _zre.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(w for w in t.split() if w not in _ZEUS_STOP)


def _zeus_sim(a, b):
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    return 0.5 * jac + 0.5 * _zSM(None, a, b).ratio()


# ----------------------------------------------------------------------------
# 1) Memória de experiência
# ----------------------------------------------------------------------------
def zeus_registrar_interacao(pergunta, resposta, origem="api"):
    """Registra uma interação e devolve seu id (use p/ enviar feedback depois)."""
    with _ZEUS_LOCK, _zeus_conn() as c:
        cur = c.execute(
            "INSERT INTO interacoes(ts,pergunta,pergunta_norm,resposta,origem) VALUES(?,?,?,?,?)",
            (_zdt.now().isoformat(timespec="seconds"), pergunta, _zeus_norm(pergunta), resposta, origem))
        return cur.lastrowid


def zeus_registrar_feedback(interacao_id, acertou, correcao=None):
    """
    Aprende com o feedback.
    acertou=True  -> promove a resposta a conhecimento e sobe a confiança.
    acertou=False -> penaliza; se vier `correcao`, ela vira o conhecimento prioritário.
    """
    with _ZEUS_LOCK, _zeus_conn() as c:
        it = c.execute("SELECT * FROM interacoes WHERE id=?", (interacao_id,)).fetchone()
        if not it:
            return False
        c.execute("UPDATE interacoes SET feedback=?, correcao=? WHERE id=?",
                  (1 if acertou else 0, correcao, interacao_id))
        agora = _zdt.now().isoformat(timespec="seconds")
        chave = it["pergunta_norm"]
        if not chave:
            return True
        if acertou:
            c.execute("""INSERT INTO conhecimento(pergunta_norm,pergunta,resposta,confianca,acertos,origem,atualizado)
                         VALUES(?,?,?,0.65,1,'validado',?)
                         ON CONFLICT(pergunta_norm) DO UPDATE SET
                           acertos=acertos+1,
                           confianca=MIN(1.0, confianca+0.1),
                           atualizado=excluded.atualizado""",
                      (chave, it["pergunta"], it["resposta"], agora))
        elif correcao and correcao.strip():
            c.execute("""INSERT INTO conhecimento(pergunta_norm,pergunta,resposta,confianca,erros,origem,atualizado)
                         VALUES(?,?,?,0.95,0,'correcao',?)
                         ON CONFLICT(pergunta_norm) DO UPDATE SET
                           resposta=excluded.resposta, confianca=0.95,
                           origem='correcao', atualizado=excluded.atualizado""",
                      (chave, it["pergunta"], correcao.strip(), agora))
        else:
            c.execute("""UPDATE conhecimento SET erros=erros+1,
                         confianca=MAX(0.0, confianca-0.25), atualizado=? WHERE pergunta_norm=?""",
                      (agora, chave))
        return True


def zeus_buscar_conhecimento(pergunta, limiar=None):
    """Melhor conhecimento local para a pergunta: (registro|None, similaridade)."""
    limiar = zeus_diretriz("limiar_correcao", 0.70) if limiar is None else limiar
    q = _zeus_norm(pergunta)
    melhor, melhor_s = None, 0.0
    with _zeus_conn() as c:
        for r in c.execute("SELECT * FROM conhecimento WHERE confianca>0"):
            s = _zeus_sim(q, r["pergunta_norm"]) * (0.7 + 0.3 * r["confianca"])
            if r["origem"] == "correcao":
                s *= 1.05  # correções têm prioridade
            if s > melhor_s:
                melhor, melhor_s = r, s
    return (dict(melhor), melhor_s) if melhor is not None and melhor_s >= limiar else (None, melhor_s)


def zeus_contexto_para_prompt(pergunta):
    """Texto com diretrizes + experiências passadas para injetar no prompt enviado à API."""
    n = int(zeus_diretriz("max_exemplos_contexto", 3))
    q = _zeus_norm(pergunta)
    with _zeus_conn() as c:
        regs = [dict(r) for r in c.execute("SELECT * FROM conhecimento WHERE confianca>=0.5")]
    regs.sort(key=lambda r: _zeus_sim(q, r["pergunta_norm"]) + (0.2 if r["origem"] == "correcao" else 0),
              reverse=True)
    ex = [r for r in regs[:n] if _zeus_sim(q, r["pergunta_norm"]) > 0.3]
    txt = f"[IDENTIDADE] {zeus_diretriz('identidade')}\n"
    if ex:
        txt += "[EXPERIÊNCIA VALIDADA - priorize isto sobre conhecimento genérico]\n"
        for r in ex:
            txt += f"- P: {r['pergunta']}\n  R: {r['resposta']}\n"
    return txt


# ----------------------------------------------------------------------------
# 2) Ciclo de reflexão crítica
# ----------------------------------------------------------------------------
def zeus_avaliar_parametros(pergunta, resposta):
    """Pontua a resposta segundo as diretrizes. Retorna dict com nota (0-1) e alertas."""
    alertas, nota = [], 1.0
    texto = resposta or ""
    if not texto.strip():
        return {"nota": 0.0, "alertas": ["resposta vazia"]}
    for pad in zeus_diretriz("frases_removidas", []):
        if _zre.search(pad, texto, _zre.I):
            alertas.append("tom genérico/corporativo detectado")
            nota -= 0.15
            break
    if len(texto) > zeus_diretriz("max_chars_resposta", 4000):
        alertas.append("resposta acima do limite")
        nota -= 0.1
    conhecido, sim = zeus_buscar_conhecimento(pergunta, 0.80)
    if conhecido and conhecido["origem"] == "correcao" and _zeus_sim(
            _zeus_norm(texto), _zeus_norm(conhecido["resposta"])) < 0.35:
        alertas.append("diverge de correção aprendida")
        nota -= 0.3
    return {"nota": max(0.0, round(nota, 2)), "alertas": alertas}


def zeus_refletir(resposta, pergunta=""):
    """
    Filtro final antes de entregar: remove boilerplate corporativo, normaliza
    o tom técnico/direto e aplica o limite de tamanho. Retorna (texto, relatório).
    """
    original = resposta or ""
    texto = original
    for pad in zeus_diretriz("frases_removidas", []):
        texto = _zre.sub(pad, "", texto, flags=_zre.I)
    texto = _zre.sub(r"[ \t]+\n", "\n", texto)
    texto = _zre.sub(r"\n{3,}", "\n\n", texto).strip()
    if texto and texto[0].islower():
        texto = texto[0].upper() + texto[1:]
    limite = int(zeus_diretriz("max_chars_resposta", 4000))
    if len(texto) > limite:
        texto = texto[:limite].rsplit(" ", 1)[0] + "…"
    if not texto:  # nunca entregar vazio: reflexão não pode apagar a resposta
        texto = original.strip()
    rel = zeus_avaliar_parametros(pergunta, texto)
    rel["alterada"] = texto != original.strip()
    return texto, rel


# ----------------------------------------------------------------------------
# 3) Autonomia: memória primeiro, API só quando necessário
# ----------------------------------------------------------------------------
def zeus_responder_autonomo(pergunta, gerador_externo=None):
    """
    Orquestrador. `gerador_externo` é uma função (prompt:str) -> str que chama
    a sua API atual. Retorna (resposta, interacao_id, origem).
    """
    zeus_init_memoria()
    conhecido, sim = zeus_buscar_conhecimento(pergunta, zeus_diretriz("limiar_memoria_local", 0.82))
    if conhecido and conhecido["confianca"] >= zeus_diretriz("confianca_minima", 0.55):
        with _ZEUS_LOCK, _zeus_conn() as c:
            c.execute("UPDATE conhecimento SET usos=usos+1 WHERE id=?", (conhecido["id"],))
        resp, _ = zeus_refletir(conhecido["resposta"], pergunta)
        return resp, zeus_registrar_interacao(pergunta, resp, "memoria_local"), "memoria_local"

    if gerador_externo is None:
        msg = "Ainda não tenho experiência validada sobre isso. Corrija-me para eu aprender."
        return msg, zeus_registrar_interacao(pergunta, msg, "sem_base"), "sem_base"

    prompt = f"{zeus_contexto_para_prompt(pergunta)}\n[PERGUNTA]\n{pergunta}"
    bruta = gerador_externo(prompt)
    resp, _ = zeus_refletir(bruta, pergunta)
    return resp, zeus_registrar_interacao(pergunta, resp, "api"), "api"


def zeus_evoluir():
    """
    Consolidação periódica da base: rebaixa conhecimento com muitos erros,
    remove o inútil e reforça o que é muito usado e validado. Retorna estatísticas.
    """
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.execute("UPDATE conhecimento SET confianca=MAX(0, confianca-0.05) WHERE erros>acertos")
        c.execute("UPDATE conhecimento SET confianca=MIN(1, confianca+0.03) WHERE acertos>=3 AND erros=0")
        apagados = c.execute("DELETE FROM conhecimento WHERE confianca<=0 AND origem!='correcao'").rowcount
        total = c.execute("SELECT COUNT(*) FROM conhecimento").fetchone()[0]
        corr = c.execute("SELECT COUNT(*) FROM conhecimento WHERE origem='correcao'").fetchone()[0]
    return {"conhecimentos": total, "correcoes": corr, "removidos": apagados}


def zeus_ensinar(pergunta, resposta):
    """Injeta conhecimento soberano manualmente (alta confiança)."""
    zeus_init_memoria()
    with _ZEUS_LOCK, _zeus_conn() as c:
        c.execute("""INSERT INTO conhecimento(pergunta_norm,pergunta,resposta,confianca,origem,atualizado)
                     VALUES(?,?,?,0.95,'correcao',?)
                     ON CONFLICT(pergunta_norm) DO UPDATE SET resposta=excluded.resposta,
                       confianca=0.95, origem='correcao', atualizado=excluded.atualizado""",
                  (_zeus_norm(pergunta), pergunta, resposta, _zdt.now().isoformat(timespec="seconds")))


# ----------------------------------------------------------------------------
# Opcional: botões de feedback (só roda se VOCÊ chamar; não altera o visual atual)
# ----------------------------------------------------------------------------
def zeus_widget_feedback(interacao_id):
    import streamlit as st
    c1, c2 = st.columns(2)
    if c1.button("✔ Acertou", key=f"zeus_ok_{interacao_id}"):
        zeus_registrar_feedback(interacao_id, True)
        st.toast("Zeus aprendeu com o acerto.")
    if c2.button("✖ Errou", key=f"zeus_err_{interacao_id}"):
        st.session_state[f"zeus_corrigir_{interacao_id}"] = True
    if st.session_state.get(f"zeus_corrigir_{interacao_id}"):
        corr = st.text_area("Resposta correta:", key=f"zeus_txt_{interacao_id}")
        if st.button("Salvar correção", key=f"zeus_save_{interacao_id}") and corr.strip():
            zeus_registrar_feedback(interacao_id, False, corr)
            st.session_state[f"zeus_corrigir_{interacao_id}"] = False
            st.toast("Correção gravada como prioridade.")



# ==============================================================================
# ZEUS v2.6 - MODO TRADER DIRETO + B2 (SEM TRAVAR EM FALLBACK DE BUSCA)
# ==============================================================================
if "_zf_b2_cliente" not in globals() and "_zf_b2_s3_resiliente" in globals():
    def _zf_b2_cliente():
        endpoint = _zf_secret_limpo("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
        if endpoint and not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        bucket = _zf_secret_limpo("B2_BUCKET")
        key_id = _zf_secret_limpo("B2_KEY_ID")
        app_key = _zf_secret_limpo("B2_APP_KEY")
        if not (bucket and key_id and app_key):
            raise RuntimeError("Credenciais B2 ausentes.")
        return _zf_b2_s3_resiliente(endpoint, key_id, app_key), bucket


if "_zf_b2_get" not in globals() and "zf_b2_get_v2" in globals():
    def _zf_b2_get(chave: str):
        try:
            dados = zf_b2_get_v2(chave)
            return dados, "ok"
        except FileNotFoundError:
            return None, "nao_encontrado"
        except Exception as e:
            return None, str(e)


if "zf_b2_salvar" not in globals() and "zf_b2_put_v2" in globals():
    zf_b2_salvar = zf_b2_put_v2


def zf_b2_salvar_seguro(chave: str, conteudo, content_type: str = "text/plain; charset=utf-8"):
    """Salva no Backblaze B2 usando as funcoes ja existentes do app (sem depender de b2sdk)."""
    fn_save = globals().get("zf_b2_salvar")
    if callable(fn_save):
        try:
            return fn_save(chave, conteudo, content_type)
        except Exception as e:
            return False, f"Erro ao salvar no B2: {e}"
    fn_cli = globals().get("_zf_b2_cliente")
    if callable(fn_cli):
        try:
            c, b = fn_cli()
            body = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
            c.put_object(Bucket=b, Key=chave, Body=body, ContentType=content_type)
            return True, f"Salvo no B2: {chave}"
        except Exception as e:
            return False, f"Erro S3/B2: {e}"
    return False, "Cliente B2 nao encontrado no app."


def _zf_ler_historico_b2_trader(simbolo: str, max_arquivos: int = 2) -> list[dict]:
    """Le os ultimos CSVs salvos no B2 com limite baixo para responder rapido."""
    simbolo = re.sub(r"[^A-Z0-9]", "", (simbolo or "BTCUSDT").upper())
    fn_cli = globals().get("_zf_b2_cliente")
    fn_get = globals().get("_zf_b2_get")
    if not (callable(fn_cli) and callable(fn_get)):
        return []
    try:
        c, b = fn_cli()
        resp = c.list_objects_v2(Bucket=b, Prefix=f"dados/binance/{simbolo}_", MaxKeys=20)
        csvs = [
            o["Key"]
            for o in resp.get("Contents", [])
            if o.get("Key", "").endswith(".csv")
        ]
        csvs.sort(reverse=True)
    except Exception:
        return []

    resumos = []
    for chave in csvs[:max_arquivos]:
        try:
            dados, estado = fn_get(chave)
            if estado != "ok" or not dados:
                continue
            linhas = list(_csv.DictReader(io.StringIO(dados.decode("utf-8", errors="ignore"))))
            closes = [float(x["close"]) for x in linhas if x.get("close")][-120:]
            highs = [float(x["high"]) for x in linhas if x.get("high")][-120:]
            lows = [float(x["low"]) for x in linhas if x.get("low")][-120:]
            vols = [float(x["volume"]) for x in linhas if x.get("volume")][-120:]
            if len(closes) >= 2:
                var_pct = ((closes[-1] / closes[0]) - 1.0) * 100.0
                sma20 = sum(closes[-20:]) / min(len(closes), 20)
                ema9 = closes[0]
                k_ema = 2.0 / (min(len(closes), 9) + 1.0)
                for v in closes:
                    ema9 = (v * k_ema) + (ema9 * (1.0 - k_ema))
                rsi14 = None
                if len(closes) >= 15:
                    deltas = [closes[i] - closes[i - 1] for i in range(-14, 0)]
                    ganhos = sum(d for d in deltas if d > 0) / 14.0
                    perdas = sum(-d for d in deltas if d < 0) / 14.0
                    rsi14 = 100.0 if perdas == 0 else round(100.0 - (100.0 / (1.0 + (ganhos / perdas))), 2)
                vol_media = round(sum(vols[-20:]) / min(len(vols), 20), 2) if vols else 0.0
                resumos.append({
                    "arquivo": chave,
                    "candles": len(closes),
                    "ultimo": closes[-1],
                    "min": min(lows) if lows else min(closes),
                    "max": max(highs) if highs else max(closes),
                    "var_pct": round(var_pct, 2),
                    "sma20": round(sma20, 4),
                    "ema9": round(ema9, 4),
                    "rsi14": rsi14,
                    "vol_media20": vol_media,
                })
        except Exception:
            continue
    return resumos


def _zf_macro_bcb_resumo() -> dict:
    """Le dados recentes de SELIC e dolar do BCB se disponivel no codigo."""
    res = {}
    fn_bcb = globals().get("zf_dados_bcb")
    if not callable(fn_bcb):
        return res
    for serie in ("selic", "dolar"):
        try:
            out = fn_bcb(serie, 5)
            if isinstance(out, (list, tuple)):
                res[serie] = out[-1] if out else ""
            elif out:
                res[serie] = str(out)[-200:]
        except Exception:
            continue
    return res


def _zf_gerar_leitura_tecnica_direta(simbolo: str, intervalo: str, ultimo: dict, macro_info: dict) -> str:
    """Gera parecer tecnico instantaneo a partir dos indicadores reais do B2 (sem depender de IA/DuckDuckGo)."""
    if not ultimo or not ultimo.get("ultimo"):
        return (
            f"Sem candles recentes para {simbolo} ({intervalo}) no B2 no momento. "
            "Verifique a coleta da Binance ou tente novamente em instantes."
        )
    px = float(ultimo["ultimo"])
    sma20 = float(ultimo.get("sma20") or px)
    ema9 = float(ultimo.get("ema9") or px)
    vmin = float(ultimo.get("min") or px)
    vmax = float(ultimo.get("max") or px)
    var_pct = float(ultimo.get("var_pct") or 0.0)
    rsi = ultimo.get("rsi14")

    if px >= sma20 and ema9 >= sma20:
        vies = "Alta (preco e EMA9 acima da SMA20)"
    elif px < sma20 and ema9 < sma20:
        vies = "Baixa (preco e EMA9 abaixo da SMA20)"
    else:
        vies = "Transicao / Consolidacao (cruzamento entre EMA9 e SMA20)"

    if rsi is None:
        rsi_txt = "RSI14 indisponivel"
    elif rsi >= 70:
        rsi_txt = f"RSI14 em `{rsi}` (sobrecompra — risco de realizacao/pullback)"
    elif rsi <= 30:
        rsi_txt = f"RSI14 em `{rsi}` (sobrevenda — possivel repique tecnico)"
    else:
        rsi_txt = f"RSI14 em `{rsi}` (zona neutra, espaco para movimento)"

    stop_compra = round(min(vmin, px * 0.985), 4)
    alvo_alta = round(max(vmax, px * 1.02), 4)
    stop_venda = round(max(vmax, px * 1.015), 4)
    alvo_baixa = round(min(vmin, px * 0.98), 4)

    bloco = [
        f"### Leitura Tecnica ({simbolo} - {intervalo})",
        f"- **Vies atual:** {vies} | Variacao no recorte: `{var_pct}%`",
        f"- **Momento:** {rsi_txt}",
        f"- **Faixa observada (B2):** Minima `{vmin}` | Maxima `{vmax}`",
        "",
        "### Cenarios e Gestao de Risco",
        f"- **Cenario de Alta:** sustentacao acima de `{round(sma20, 4)}` mira `{alvo_alta}`, com invalidacao/stop abaixo de `{stop_compra}` (risco max. sugerido: 1% a 2% da banca).",
        f"- **Cenario de Baixa:** perda de `{round(sma20, 4)}` abre espaco ate `{alvo_baixa}`, com invalidacao/stop acima de `{stop_venda}`."
    ]
    if macro_info:
        bloco.append("")
        bloco.append(f"### Contexto Macro (BCB): `{json.dumps(macro_info, ensure_ascii=False)}`")
    return "\n".join(bloco)


def zf_modo_trader_b2(prompt_usuario: str) -> str:
    """Modo Trader direto e instantaneo: coleta Binance, le B2, gera leitura tecnica e salva no B2."""
    low = (prompt_usuario or "").lower()
    mapa_pares = {
        "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
        "ethereum": "ETHUSDT", "eth": "ETHUSDT",
        "solana": "SOLUSDT", "sol": "SOLUSDT",
        "bnb": "BNBUSDT",
        "xrp": "XRPUSDT", "ripple": "XRPUSDT",
        "ada": "ADAUSDT", "cardano": "ADAUSDT",
        "doge": "DOGEUSDT", "dogecoin": "DOGEUSDT",
    }
    m_sim = re.search(r"\b([A-Za-z]{2,8}USDT)\b", prompt_usuario or "")
    simbolo = (
        m_sim.group(1).upper()
        if m_sim
        else next((v for k, v in mapa_pares.items() if re.search(rf"\b{k}\b", low)), "BTCUSDT")
    )
    m_itv = re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)\b", low)
    intervalo = m_itv.group(1) if m_itv else "1h"

    log_b2 = []
    fn_bin = globals().get("zf_dados_binance")
    if callable(fn_bin):
        try:
            r_bin = fn_bin(simbolo=simbolo, intervalo=intervalo, limite=100, forcar=True)
            if isinstance(r_bin, list) and r_bin:
                log_b2.append(str(r_bin[-1]))
            elif r_bin:
                log_b2.append(str(r_bin))
        except Exception as e:
            log_b2.append(f"Binance: {type(e).__name__}")

    macro_info = {}
    if re.search(r"\b(selic|ipca|d[oó]lar|cdi|juros|macro|brasil|investimento)\b", low):
        macro_info = _zf_macro_bcb_resumo()

    hist_b2 = _zf_ler_historico_b2_trader(simbolo, max_arquivos=2)
    ultimo = hist_b2[0] if hist_b2 else {}

    parecer_tecnico = _zf_gerar_leitura_tecnica_direta(simbolo, intervalo, ultimo, macro_info)

    if re.search(r"\b(usar ia|com ia|noticia|not[ií]cia|web|internet)\b", low):
        fn_base = globals().get("_zf_processar_prompt_base")
        if callable(fn_base):
            try:
                resp_ia = str(fn_base(f"Resumo curto de mercado cripto {simbolo}: {prompt_usuario.strip()}") or "").strip()
                if resp_ia and "As IAs nao responderam" not in resp_ia and "As IAs não responderam" not in resp_ia:
                    parecer_tecnico += "\n\n### Complemento IA\n" + resp_ia
            except Exception:
                pass

    ts = _zf_agora() if callable(globals().get("_zf_agora")) else "atual"
    utc_str = (
        _zf_agora_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
        if callable(globals().get("_zf_agora_utc"))
        else "UTC indisponivel"
    )
    relatorio = (
        f"# Trader Zeus - {simbolo} ({intervalo})\n"
        f"- **UTC:** {utc_str}\n"
        f"- **Dados B2:** {json.dumps(ultimo, ensure_ascii=False)}\n\n"
        f"{parecer_tecnico}\n"
    )
    ok_md, msg_md = zf_b2_salvar_seguro(
        f"autonomia/trader/analise_{simbolo.lower()}_{intervalo}_{ts}.md",
        relatorio,
        "text/markdown; charset=utf-8",
    )
    log_b2.append(msg_md if ok_md else f"Aviso: {msg_md}")

    payload_json = {
        "simbolo": simbolo,
        "intervalo": intervalo,
        "utc": utc_str,
        "resumo_b2": ultimo,
        "macro_bcb": macro_info,
    }
    zf_b2_salvar_seguro(
        f"autonomia/trader/ultimo_{simbolo.lower()}.json",
        json.dumps(payload_json, ensure_ascii=False, indent=2),
        "application/json; charset=utf-8",
    )

    linhas_out = [f"**Trader Zeus - {simbolo} ({intervalo})**"]
    if ultimo:
        linhas_out.append(
            f"- **B2:** ultimo `{ultimo.get('ultimo')}` | SMA20 `{ultimo.get('sma20')}` | EMA9 `{ultimo.get('ema9')}` | RSI14 `{ultimo.get('rsi14')}` | var `{ultimo.get('var_pct')}%`"
        )
    if log_b2:
        linhas_out.append("- **B2:** " + " | ".join(str(x) for x in log_b2[-2:]))
    linhas_out.append("\n" + parecer_tecnico)
    return "\n".join(linhas_out)


def _zr_flexivel_trader_ou_pesquisa(t: str, low: str) -> str | None:
    gatilho_trader = re.search(
        r"\b(trad\w*|opera\w*\s+mercado|analis\w*\s+(?:o\s+)?(?:mercado|gr[aá]fico|btc|bitcoin|eth|cripto|ativo)|"
        r"estrat[eé]gia|investimento|carteira|risco|stop|rsi|ema|sma)\b",
        low,
    )
    gatilho_b2_mercado = re.search(
        r"\b(pesquis\w*|busc\w*|colet\w*|baix\w*|pux\w*|atualiz\w*)\b",
        low,
    ) and re.search(
        r"\b(binance|btc|bitcoin|eth|ethereum|cripto|gr[aá]ficos?|bcb|selic|d[oó]lar|cofre|b2)\b",
        low,
    )
    if gatilho_trader or gatilho_b2_mercado:
        return zf_modo_trader_b2(t)
    return None


def _zf_interceptar_roteamento_chat_trader(prompt_usuario: str) -> str | None:
    """Roteamento direto no chat para pedidos de trader, mercado ou B2 sem cair na conversa genérica."""
    t = (prompt_usuario or "").strip()
    if not t:
        return None
    low = t.lower()
    if re.search(r"\b(trad\w*|opera\w*\s+mercado|analis\w*\s+(?:o\s+)?(?:mercado|gr[aá]fico|btc|bitcoin|eth|ethusdt|btcusdt|sol|solusdt|cripto|ativo)|estrat[eé]gia|investimento|carteira|risco|stop|rsi|ema|sma|cofre|b2|selic|d[oó]lar)\b", low):
        try:
            return zf_modo_trader_b2(t)
        except Exception as e:
            return f"**Trader Zeus - Roteamento B2:** erro ao executar análise direta (`{type(e).__name__}: {e}`)."
    return _zr_flexivel_trader_ou_pesquisa(t, low)


def _zf_envolver_roteador_chat_existente():
    """Garante que o roteador principal do chat chame o modo trader/B2 antes de cair em LLM genérica."""
    for nome_fn in ("_zf_processar_prompt_base", "processar_prompt", "responder_chat", "zf_responder_chat"):
        fn_orig = globals().get(nome_fn)
        if callable(fn_orig) and not getattr(fn_orig, "_zf_trader_wrap", False):
            def _wrapper(prompt, *args, _orig=fn_orig, **kwargs):
                texto = prompt if isinstance(prompt, str) else str(prompt or "")
                try:
                    resp_direta = _zf_interceptar_roteamento_chat_trader(texto)
                    if resp_direta:
                        return resp_direta
                except Exception:
                    pass
                return _orig(prompt, *args, **kwargs)
            _wrapper._zf_trader_wrap = True
            globals()[nome_fn] = _wrapper


def zf_render_painel_trader_b2():
    """Painel rapido no Streamlit para rodar o Modo Trader + B2 direto na tela."""
    with st.expander("Trader Zeus + B2", expanded=False):
        c1, c2 = st.columns([2, 1])
        with c1:
            par_tr = st.text_input("Par ou ativo (ex: BTCUSDT, ETHUSDT, SOLUSDT):", value="BTCUSDT", key="zf_tr_par")
        with c2:
            itv_tr = st.selectbox(
                "Timeframe",
                ["5m", "15m", "1h", "4h", "1d"],
                index=2,
                key="zf_tr_itv",
            )
        foco_tr = st.text_input(
            "Foco ou pergunta (opcional):",
            placeholder="Ex: cenarios, risco e macro SELIC/dolar",
            key="zf_tr_foco",
        )
        if st.button("Analisar e Salvar no B2", key="zf_tr_btn_rodar", use_container_width=True):
            pergunta = f"Analisar {par_tr.strip() or 'BTCUSDT'} no timeframe {itv_tr}. {foco_tr.strip()}".strip()
            with st.spinner("Coletando dados da Binance/B2 e gerando analise..."):
                resp_tr = zf_modo_trader_b2(pergunta)
            st.markdown(resp_tr)


try:
    if callable(globals().get("_zf_envolver_roteador_chat_existente")):
        _zf_envolver_roteador_chat_existente()
except Exception:
    pass


try:
    if callable(globals().get("zf_render_painel_trader_b2")):
        zf_render_painel_trader_b2()
except Exception as _zf_err_tr_ui:
    st.caption("Trader Zeus + B2: " + str(_zf_err_tr_ui)[:140])


# ==============================================================================
# CAMADA 1 - COFRE B2 RESILIENTE (S3 + FALLBACK NATIVO SEM QUEBRAR)
# Cole este bloco no final do app.py
# ==============================================================================
import html as _zf_html
import urllib.error as _zf_uerr
import urllib.parse as _zf_uparse
import urllib.request as _zf_ureq
from difflib import SequenceMatcher as _zf_SeqMatcher

_ZF_PROTEGIDOS = {"banco/zeus_chat.db", "autonomo/diario.json", "memoria/fatos.json"}


def _zf_secret_limpo(chave: str, padrao: str = "") -> str:
    try:
        sec = st.secrets
        val = sec.get(chave)
        if not val:
            for _, bloco in sec.items():
                if hasattr(bloco, "get"):
                    val = bloco.get(chave)
                    if val:
                        break
        if val:
            v = re.sub(r"\s+", "", str(val)).strip("\"'")
            return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v
    except Exception:
        pass
    v = re.sub(r"\s+", "", str(os.environ.get(chave) or padrao)).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v


def _zf_detalhe_erro_b2(e: Exception) -> str:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error") or {}
    base = f"{type(e).__name__}: {e}"
    if err.get("Code"):
        base += f" [{err.get('Code')}]"
    return base[:300]


def _zf_b2_nao_encontrado(e: Exception) -> bool:
    resp = getattr(e, "response", None) or {}
    cod = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if cod == "NoSuchBucket":
        return False
    return cod in ("NoSuchKey", "404", "NotFound") or status == 404 or type(e).__name__ == "FileNotPresent"


@st.cache_resource
def _zf_b2_s3_resiliente(endpoint: str, key_id: str, app_key: str):
    import boto3
    from botocore.config import Config
    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    base = dict(
        signature_version="s3v4",
        connect_timeout=10,
        read_timeout=25,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    try:
        cfg = Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            **base,
        )
    except TypeError:
        cfg = Config(**base)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name=m.group(1) if m else None,
        config=cfg,
    )


def _zf_b2_exec(fn_s3):
    endpoint = _zf_secret_limpo("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    bucket = _zf_secret_limpo("B2_BUCKET")
    key_id = _zf_secret_limpo("B2_KEY_ID")
    app_key = _zf_secret_limpo("B2_APP_KEY")
    if not (bucket and key_id and app_key):
        raise RuntimeError("Cofre B2 sem credenciais nos Secrets (B2_BUCKET, B2_KEY_ID, B2_APP_KEY).")
    try:
        return fn_s3(_zf_b2_s3_resiliente(endpoint, key_id, app_key), bucket)
    except Exception as e:
        if _zf_b2_nao_encontrado(e):
            raise FileNotFoundError("Arquivo nao encontrado no cofre B2.") from e
        raise RuntimeError(_zf_detalhe_erro_b2(e)) from e


def zf_b2_put_v2(chave: str, dados: bytes | str, ctype: str = "application/octet-stream"):
    body = dados.encode("utf-8") if isinstance(dados, str) else dados
    _zf_b2_exec(lambda s3, b: s3.put_object(Bucket=b, Key=chave, Body=body, ContentType=ctype))
    return True, f"Salvo no B2: {chave}"


def zf_b2_get_v2(chave: str) -> bytes:
    return _zf_b2_exec(lambda s3, b: s3.get_object(Bucket=b, Key=chave)["Body"].read())


def zf_b2_list_v2(prefixo: str = "", limite: int = 40) -> list[tuple[str, int]]:
    def _s3(c, b):
        r = c.list_objects_v2(Bucket=b, Prefix=prefixo, MaxKeys=limite)
        return [(o["Key"], o.get("Size", 0)) for o in r.get("Contents", [])]
    return _zf_b2_exec(_s3)


def zf_b2_delete_v2(chave: str) -> tuple[bool, str]:
    if chave in _ZF_PROTEGIDOS:
        return False, f"Arquivo protegido: {chave}"
    def _s3(c, b):
        c.head_object(Bucket=b, Key=chave)
        c.delete_object(Bucket=b, Key=chave)
    _zf_b2_exec(_s3)
    return True, f"Apagado do B2: {chave}"
# ==============================================================================
# CAMADA 2 - MEMORIA EVOLUTIVA NO B2 (COM CACHE SEGURO E SEM SOBRESCRITA CEGA)
# Cole abaixo da Camada 1 no final do app.py
# ==============================================================================
_ZF_MEM_KEY = "memoria/fatos.json"


def _zf_norm_txt(txt: str) -> str:
    t = unicodedata.normalize("NFKD", str(txt or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", " ", t)


def zf_mem_ler(forcar: bool = False) -> list[dict]:
    cache = st.session_state.get("_zf_mem_cache")
    if cache and not forcar and (time.time() - cache["ts"] < 120):
        return cache["fatos"]
    try:
        raw = zf_b2_get_v2(_ZF_MEM_KEY)
        fatos = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception:
        return (cache or {}).get("fatos", [])
    st.session_state["_zf_mem_cache"] = {"ts": time.time(), "fatos": fatos}
    return fatos


def zf_mem_ensinar(pergunta: str, resposta: str) -> str:
    try:
        fatos = json.loads(zf_b2_get_v2(_ZF_MEM_KEY).decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception as e:
        return f"Nao consegui ler a memoria (nao sobrescrevi): {_zf_detalhe_erro_b2(e)}"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fatos.append({"p": pergunta.strip(), "r": resposta.strip(), "ts": ts})
    fatos = fatos[-500:]
    try:
        zf_b2_put_v2(_ZF_MEM_KEY, json.dumps(fatos, ensure_ascii=False, indent=1), "application/json; charset=utf-8")
    except Exception as e:
        return f"Nao consegui gravar a memoria: {_zf_detalhe_erro_b2(e)}"
    st.session_state["_zf_mem_cache"] = {"ts": time.time(), "fatos": fatos}
    return f"Aprendido e guardado no cofre B2 ({len(fatos)} fatos na memoria)."


def _zf_mem_sim(a: str, b: str) -> float:
    stop = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "que", "eh", "me", "se", "no", "na"}
    wa = " ".join(w for w in _zf_norm_txt(a).split() if w not in stop)
    wb = " ".join(w for w in _zf_norm_txt(b).split() if w not in stop)
    ta, tb = set(wa.split()), set(wb.split())
    if not ta or not tb:
        return 0.0
    return 0.5 * len(ta & tb) / len(ta | tb) + 0.5 * _zf_SeqMatcher(None, wa, wb).ratio()


def zf_mem_contexto(pergunta: str, n: int = 4) -> str:
    fatos = zf_mem_ler()
    if not fatos:
        return ""
    pontuados = sorted(((_zf_mem_sim(pergunta, f.get("p", "")), f) for f in fatos), key=lambda x: x[0], reverse=True)
    uteis = [f for s, f in pontuados[:n] if s >= 0.2]
    uteis += [f for f in fatos[-2:] if f not in uteis]
    if not uteis:
        return ""
    linhas = ["[MEMORIA ENSINADA PELO CRIADOR - PRIORIZAR]"]
    for f in uteis[: n + 2]:
        linhas.append(f"- {f['p']}" + (f" => {f['r']}" if f.get("r") != f.get("p") else ""))
    return "\n".join(linhas)
# ==============================================================================
# CAMADA 3 - MERCADO (BINANCE + BCB + GRAFICO SVG + LEITURA TECNICA DIRETA)
# Cole abaixo da Camada 2 no final do app.py
# ==============================================================================
_ZF_BINANCE_URL = "https://data-api.binance.vision/api/v3"
_ZF_BIN_ALIAS = [
    ("bitcoin", "BTCUSDT"), ("btc", "BTCUSDT"),
    ("ethereum", "ETHUSDT"), ("eth", "ETHUSDT"),
    ("bnb", "BNBUSDT"), ("solana", "SOLUSDT"), ("sol", "SOLUSDT"),
    ("xrp", "XRPUSDT"), ("cardano", "ADAUSDT"), ("ada", "ADAUSDT"),
    ("dogecoin", "DOGEUSDT"), ("doge", "DOGEUSDT"),
]
_ZF_BCB_ALIAS = [("selic diaria", 11), ("selic", 432), ("ipca", 433), ("inflacao", 433), ("dolar", 1), ("cdi", 12)]
_ZF_BCB_NOMES = {432: "Meta Selic", 433: "IPCA mensal", 1: "Dolar (venda)", 12: "CDI diario", 11: "Selic diaria"}


def _zf_http_rapido(url: str, timeout: int = 15) -> tuple[int, str]:
    try:
        req = _zf_ureq.Request(url, headers={"User-Agent": "Mozilla/5.0 (Zeus/3.0)"}, method="GET")
        with _zf_ureq.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except _zf_uerr.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _zf_svg_linha(titulo: str, valores: list[float], ini: str, fim: str) -> str:
    w, h, p = 800, 320, 44
    n = len(valores)
    mn, mx = min(valores), max(valores)
    span = (mx - mn) or 1.0
    pts = " ".join(
        f"{p + i * (w - 2 * p) / max(1, n - 1):.1f},{h - p - (v - mn) / span * (h - 2 * p):.1f}"
        for i, v in enumerate(valores)
    )
    t = _zf_html.escape(titulo)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="#0b0f19"/>'
        f'<text x="{p}" y="24" fill="#00ffcc" font-family="monospace" font-size="15">{t}</text>'
        f'<polyline fill="none" stroke="#00ffcc" stroke-width="2" points="{pts}"/>'
        f'<text x="{p}" y="{h - 12}" fill="#8aa" font-family="monospace" font-size="11">{_zf_html.escape(ini)}</text>'
        f'<text x="{w - p}" y="{h - 12}" fill="#8aa" font-family="monospace" font-size="11" text-anchor="end">{_zf_html.escape(fim)}</text>'
        f'<text x="{p}" y="{h - p - 6}" fill="#8aa" font-family="monospace" font-size="11">min {mn:g}</text>'
        f'<text x="{w - p}" y="{p + 4}" fill="#8aa" font-family="monospace" font-size="11" text-anchor="end">max {mx:g}</text>'
        f"</svg>"
    )


def zf_coletar_analisar_binance(simbolo: str = "BTCUSDT", intervalo: str = "1h", limite: int = 120) -> tuple[str, dict]:
    simbolo = re.sub(r"[^A-Z0-9]", "", (simbolo or "BTCUSDT").upper())
    q = _zf_uparse.urlencode({"symbol": simbolo, "interval": intervalo, "limit": min(max(limite, 20), 500)})
    st_code, txt = _zf_http_rapido(f"{_ZF_BINANCE_URL}/klines?{q}")
    if st_code != 200:
        return f"Binance {simbolo}: HTTP {st_code} {txt[:100]}", {}
    velas = json.loads(txt)
    if not isinstance(velas, list) or len(velas) < 2:
        return f"Binance {simbolo}: resposta vazia.", {}

    ms_iso = lambda ms: datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["open_time", "open", "high", "low", "close", "volume", "close_time"])
    for k in velas:
        w.writerow([ms_iso(k[0]), k[1], k[2], k[3], k[4], k[5], ms_iso(k[6])])

    closes = [float(k[4]) for k in velas]
    highs = [float(k[2]) for k in velas]
    lows = [float(k[3]) for k in velas]
    ini, fim = ms_iso(velas[0][0]), ms_iso(velas[-1][0])

    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    base = f"dados/binance/{simbolo}_{intervalo}"
    zf_b2_put_v2(f"{base}_{ts_tag}.csv", buf.getvalue(), "text/csv; charset=utf-8")
    zf_b2_put_v2(f"{base}_ultimo.csv", buf.getvalue(), "text/csv; charset=utf-8")
    svg = _zf_svg_linha(f"{simbolo} {intervalo} fechamento", closes, ini, fim)
    zf_b2_put_v2(f"dados/graficos/{simbolo}_{intervalo}.svg", svg, "image/svg+xml")

    sma20 = sum(closes[-20:]) / min(len(closes), 20)
    ema9 = closes[0]
    k_ema = 2.0 / (min(len(closes), 9) + 1.0)
    for v in closes:
        ema9 = (v * k_ema) + (ema9 * (1.0 - k_ema))
    rsi14 = None
    if len(closes) >= 15:
        deltas = [closes[i] - closes[i - 1] for i in range(-14, 0)]
        ganhos = sum(d for d in deltas if d > 0) / 14.0
        perdas = sum(-d for d in deltas if d < 0) / 14.0
        rsi14 = 100.0 if perdas == 0 else round(100.0 - (100.0 / (1.0 + (ganhos / perdas))), 2)

    resumo = {
        "simbolo": simbolo,
        "intervalo": intervalo,
        "ultimo": closes[-1],
        "min": min(lows),
        "max": max(highs),
        "sma20": round(sma20, 4),
        "ema9": round(ema9, 4),
        "rsi14": rsi14,
        "var_pct": round(((closes[-1] / closes[0]) - 1.0) * 100.0, 2),
        "periodo": f"{ini} -> {fim}",
    }
    return f"Salvo CSV + SVG de {simbolo} ({intervalo}) no B2.", resumo


def zf_coletar_bcb_sgs(codigo: int, n: int = 10) -> str:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{min(n, 20)}?formato=json"
    st_code, txt = _zf_http_rapido(url)
    if st_code != 200:
        return f"BCB serie {codigo}: HTTP {st_code}"
    dados = json.loads(txt)
    if not isinstance(dados, list) or not dados:
        return f"BCB serie {codigo}: vazio."
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    zf_b2_put_v2(f"dados/bcb/sgs_{codigo}_{ts_tag}.json", json.dumps(dados, ensure_ascii=False), "application/json")
    zf_b2_put_v2(f"dados/bcb/sgs_{codigo}_ultimo.json", json.dumps(dados, ensure_ascii=False), "application/json")
    nome = _ZF_BCB_NOMES.get(codigo, f"SGS {codigo}")
    return f"{nome}: {dados[-1].get('valor')} ({dados[-1].get('data')})"


def zf_trader_rapido_b2(prompt_usuario: str) -> str:
    low = _zf_norm_txt(prompt_usuario)
    m_sim = re.search(r"\b([a-z0-9]{2,8}usdt)\b", low)
    simbolo = m_sim.group(1).upper() if m_sim else next(
        (s for k, s in _ZF_BIN_ALIAS if re.search(rf"\b{k}\b", low)), "BTCUSDT"
    )
    m_itv = re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)\b", low)
    intervalo = m_itv.group(1) if m_itv else "1h"

    msg_bin, r = zf_coletar_analisar_binance(simbolo, intervalo, limite=120)
    macros = []
    if re.search(r"\b(selic|ipca|dolar|cdi|juros|macro|bcb)\b", low):
        for cod in (432, 1):
            macros.append(zf_coletar_bcb_sgs(cod, 5))

    if not r:
        return f"Nao foi possivel obter candles de {simbolo} ({intervalo}): {msg_bin}"

    px, sma, ema, rsi = r["ultimo"], r["sma20"], r["ema9"], r["rsi14"]
    vies = "Alta (preco e EMA9 acima da SMA20)" if (px >= sma and ema >= sma) else (
        "Baixa (preco e EMA9 abaixo da SMA20)" if (px < sma and ema < sma) else "Consolidacao / Cruzamento"
    )
    stop_c = round(min(r["min"], px * 0.985), 4)
    alvo_a = round(max(r["max"], px * 1.02), 4)
    mem_ctx = zf_mem_contexto(prompt_usuario, n=3)

    relatorio = [
        f"### Trader Zeus - {simbolo} ({intervalo})",
        f"- **Preco:** `{px}` | **SMA20:** `{sma}` | **EMA9:** `{ema}` | **RSI14:** `{rsi}` | **Var:** `{r['var_pct']}%`",
        f"- **Vies:** {vies} | **Faixa:** `{r['min']}` a `{r['max']}`",
        f"- **Gestao de Risco:** sustentacao acima de `{sma}` mira `{alvo_a}`, com stop/invalidacao abaixo de `{stop_c}`.",
        f"- **Cofre B2:** {msg_bin}",
    ]
    if macros:
        relatorio.append("- **Macro BCB:** " + " | ".join(macros))
    if mem_ctx:
        relatorio.append("\n" + mem_ctx)

    texto_final = "\n".join(relatorio)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zf_b2_put_v2(f"autonomia/trader/analise_{simbolo.lower()}_{intervalo}_{ts_tag}.md", texto_final, "text/markdown; charset=utf-8")
    return texto_final
# ==============================================================================
# CAMADA 4 - DIAGNOSTICO RAPIDO + MINI PAINEL TRADER/DIAGNOSTICO (SEM DUPLICAR SIDEBAR)
# Cole abaixo da Camada 3 no final do app.py
# ==============================================================================
def zf_diagnostico_rapido() -> str:
    linhas = ["**Diagnostico Rapido do Zeus:**"]
    try:
        zf_b2_put_v2("diagnostico/_ping.txt", b"ok", "text/plain")
        ok = zf_b2_get_v2("diagnostico/_ping.txt") == b"ok"
        zf_b2_delete_v2("diagnostico/_ping.txt")
        linhas.append("- **Cofre B2:** OK (gravacao, leitura e exclusao funcionando)" if ok else "- **Cofre B2:** Falha na verificacao")
    except Exception as e:
        linhas.append(f"- **Cofre B2:** Erro -> {_zf_detalhe_erro_b2(e)}")

    try:
        _, info = zf_coletar_analisar_binance("BTCUSDT", "1h", limite=30)
        linhas.append(f"- **Binance + B2:** OK (BTCUSDT ultimo `{info.get('ultimo')}`)" if info else "- **Binance + B2:** Sem retorno")
    except Exception as e:
        linhas.append(f"- **Binance + B2:** Erro -> {type(e).__name__}")

    fatos = zf_mem_ler(forcar=True)
    linhas.append(f"- **Memoria B2:** {len(fatos)} fato(s) carregado(s)")
    return "\n".join(linhas)


def zf_render_camadas_extras():
    with st.expander("Zeus B2: Trader Rapido, Memoria & Diagnostico", expanded=False):
        tab_tr, tab_mem, tab_diag = st.tabs(["Trader B2", "Ensinar Memoria", "Diagnostico"])
        with tab_tr:
            c1, c2 = st.columns([2, 1])
            with c1:
                par = st.text_input("Par (ex: BTCUSDT, ETHUSDT, SOLUSDT):", value="BTCUSDT", key="zf_c4_par")
            with c2:
                itv = st.selectbox("Timeframe", ["5m", "15m", "1h", "4h", "1d"], index=2, key="zf_c4_itv")
            incluir_macro = st.checkbox("Incluir SELIC e Dolar (BCB)", value=True, key="zf_c4_macro")
            if st.button("Analisar & Salvar CSV/SVG/MD no B2", key="zf_c4_btn_tr", use_container_width=True):
                q = f"Analisar {par} em {itv}" + (" com macro selic e dolar" if incluir_macro else "")
                with st.spinner("Processando direto na Binance + B2..."):
                    st.markdown(zf_trader_rapido_b2(q))
        with tab_mem:
            p_mem = st.text_input("Pergunta / Gatilho:", placeholder="Ex: Qual minha regra de stop?", key="zf_c4_mp")
            r_mem = st.text_area("Regra / Resposta para salvar no B2:", placeholder="Ex: Nunca arriscar mais de 1.5% por operacao.", key="zf_c4_mr")
            if st.button("Salvar na Memoria B2", key="zf_c4_btn_mem", use_container_width=True) and p_mem.strip() and r_mem.strip():
                st.success(zf_mem_ensinar(p_mem, r_mem))
            if st.button("Ver Ultimos Fatos", key="zf_c4_btn_list", use_container_width=True):
                st.json(zf_mem_ler(forcar=True)[-5:])
        with tab_diag:
            if st.button("Rodar Diagnostico Agora", key="zf_c4_btn_diag", use_container_width=True):
                with st.spinner("Testando B2, Binance e Memoria..."):
                    st.markdown(zf_diagnostico_rapido())


try:
    zf_render_camadas_extras()
except Exception as _zf_c4_err:
    st.caption(f"Camadas Zeus B2: {_zf_c4_err}")
# Ponte de compatibilidade: faz o bloco "Trader Zeus + B2" enxergar a Camada 1
if "zf_b2_salvar" not in globals() and "zf_b2_put_v2" in globals():
  zf_b2_salvar = zf_b2_put_v2
if "zf_b2_ler" not in globals() and "zf_b2_get_v2" in globals():
  zf_b2_ler = zf_b2_get_v2
if "zf_b2_listar" not in globals() and "zf_b2_list_v2" in globals():
  zf_b2_listar = zf_b2_list_v2


def zf_b2_ler_csv_candles(simbolo: str = "BTCUSDT", intervalo: str = "1h"):
  """Busca o CSV mais recente no B2; se não achar, puxa direto da Binance."""
  sim = re.sub(r"[^A-Z0-9]", "", (simbolo or "BTCUSDT").upper())
  itv = (intervalo or "1h").strip()
  if "zf_b2_get_v2" in globals():
    for chave in (
        f"dados/binance/{sim}_{itv}_ultimo.csv",
        f"binance/{sim}_{itv}_ultimo.csv",
        f"candles/{sim}_{itv}.csv",
    ):
      try:
        raw = zf_b2_get_v2(chave)
        txt = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if txt.strip():
          return txt, f"B2 ({chave})"
      except FileNotFoundError:
        pass
      except Exception:
        pass
  if "zf_coletar_analisar_binance" in globals():
    msg, resumo = zf_coletar_analisar_binance(sim, itv, limite=120)
    if resumo:
      try:
        raw = zf_b2_get_v2(f"dados/binance/{sim}_{itv}_ultimo.csv")
        return raw.decode("utf-8", errors="replace"), f"Binance -> B2 ({msg})"
      except Exception:
        return "", msg
  return "", "Sem fonte de candles disponível."
# ==============================================================================
# ZEUS v3.0 - CORRECAO DE CHAVES/ROTAS DE IA + ROTEAMENTO TRADER/B2 NO CHAT
# ==============================================================================
def _zf_chaves_ia_resumo() -> dict:
    """Diagnostica quais chaves de IA estao presentes e validas sem expor segredos."""
    chaves = ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY")
    ativas = []
    for k in chaves:
        val = ""
        try:
            if "_zf_secret_limpo" in globals() and callable(globals()["_zf_secret_limpo"]):
                val = str(globals()["_zf_secret_limpo"](k, "") or "").strip()
            elif "_secret" in globals() and callable(globals()["_secret"]):
                val = str(globals()["_secret"](k) or "").strip()
            else:
                val = str(st.secrets.get(k, "") if hasattr(st, "secrets") else "").strip()
        except Exception:
            val = ""
        if val and len(val) >= 8 and not val.lower().startswith(("coloque_", "sua_", "insira_", "replace_", "none", "null")):
            ativas.append(k)
    return {"chaves_ativas": ativas, "total": len(ativas)}


def _zf_limpar_resposta_ia_invalida(texto: str) -> str:
    """Descarta respostas de erro, bloqueio de chave/quota ou mensagem padrao de falha."""
    t = (texto or "").strip()
    if not t:
        return ""
    sinais_erro = (
        "não consegui contatar as ias",
        "nao consegui contatar as ias",
        "as ias não responderam",
        "as ias nao responderam",
        "api key not valid",
        "invalid api key",
        "401 unauthorized",
        "403 forbidden",
        "429 too many requests",
        "resource_exhausted",
        "quota exceeded",
        "insufficient_quota",
        "connection error",
        "connecttimeout",
        "readtimeout",
    )
    low = t.lower()
    if any(s in low for s in sinais_erro):
        return ""
    return t


def _zf_chamada_gemini_direta(prompt_texto: str) -> str:
    """Chamada direta e limpa ao Gemini usando as chaves/modelos configurados."""
    api_key = ""
    for nome_k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        try:
            if "_zf_secret_limpo" in globals() and callable(globals()["_zf_secret_limpo"]):
                api_key = str(globals()["_zf_secret_limpo"](nome_k, "") or "").strip()
            elif "_secret" in globals() and callable(globals()["_secret"]):
                api_key = str(globals()["_secret"](nome_k) or "").strip()
        except Exception:
            api_key = ""
        if api_key:
            break
    if not api_key:
        return ""

    modelos = []
    for m_k in ("GEMINI_MODEL", "GEMINI_SEARCH_MODEL"):
        try:
            m_val = str(globals()["_zf_secret_limpo"](m_k, "") or "").strip() if callable(globals().get("_zf_secret_limpo")) else ""
            if m_val and m_val not in modelos:
                modelos.append(m_val)
        except Exception:
            pass
    for padrao in ("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"):
        if padrao not in modelos:
            modelos.append(padrao)

    for modelo in modelos:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt_texto}]}],
                    "generationConfig": {"temperature": 0.4},
                },
                timeout=35,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                continue
            parts = ((cands[0].get("content") or {}).get("parts")) or []
            out = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            out = _zf_limpar_resposta_ia_invalida(out)
            if out:
                return out
        except Exception:
            continue
    return ""


def _zf_chamada_groq_direta(prompt_texto: str) -> str:
    """Fallback rapido via Groq caso exista GROQ_API_KEY valida."""
    api_key = ""
    try:
        if callable(globals().get("_zf_secret_limpo")):
            api_key = str(globals()["_zf_secret_limpo"]("GROQ_API_KEY", "") or "").strip()
        elif callable(globals().get("_secret")):
            api_key = str(globals()["_secret"]("GROQ_API_KEY") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        return ""
    for modelo in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": modelo,
                    "messages": [{"role": "user", "content": prompt_texto}],
                    "temperature": 0.4,
                },
                timeout=30,
            )
            if r.status_code != 200:
                continue
            choices = r.json().get("choices") or []
            if not choices:
                continue
            msg = ((choices[0].get("message") or {}).get("content") or "").strip()
            msg = _zf_limpar_resposta_ia_invalida(msg)
            if msg:
                return msg
        except Exception:
            continue
    return ""


def _zf_rotas_ia_resilientes(prompt_texto: str, fn_legada=None, args=(), kwargs=None) -> str:
    """Executa rotas de IA em ordem resiliente (funcao legada valida -> Gemini -> Groq) sem vazar erro tecnico."""
    kwargs = kwargs or {}
    if callable(fn_legada):
        try:
            resp_leg = fn_legada(prompt_texto, *args, **kwargs)
            resp_limpa = _zf_limpar_resposta_ia_invalida(str(resp_leg or ""))
            if resp_limpa:
                return resp_limpa
        except Exception:
            pass

    for fn_direta in (_zf_chamada_gemini_direta, _zf_chamada_groq_direta):
        try:
            resp_dir = fn_direta(prompt_texto)
            if resp_dir:
                return resp_dir
        except Exception:
            continue

    diag = _zf_chaves_ia_resumo()
    if diag["total"] == 0:
        return (
            "Nenhuma chave de IA válida foi encontrada nos Secrets (`GEMINI_API_KEY` ou `GROQ_API_KEY`). "
            "Adicione uma chave ativa nos Secrets do Streamlit para o chat principal responder."
        )
    return (
        f"As rotas de IA ({', '.join(diag['chaves_ativas'])}) não responderam agora "
        "(verifique quota, validade da chave ou conexão de rede)."
    )


def _zf_envolver_roteador_chat_v3():
    """Envolve as funcoes de processamento do chat para priorizar Trader/B2 e blindar a rota de IA."""
    candidatas = (
        "_zf_processar_prompt_base",
        "processar_prompt",
        "responder_chat",
        "zf_responder_chat",
        "gerar_resposta_ia",
        "responder_ia",
    )
    for nome_fn in candidatas:
        fn_orig = globals().get(nome_fn)
        if callable(fn_orig) and not getattr(fn_orig, "_zf_v3_ia_wrap", False):
            def _wrapper(prompt, *args, _orig=fn_orig, **kwargs):
                texto = prompt if isinstance(prompt, str) else str(prompt or "")
                try:
                    if callable(globals().get("_zf_interceptar_roteamento_chat_trader")):
                        resp_tr = globals()["_zf_interceptar_roteamento_chat_trader"](texto)
                        if resp_tr:
                            return resp_tr
                except Exception:
                    pass
                return _zf_rotas_ia_resilientes(texto, fn_legada=_orig, args=args, kwargs=kwargs)
            _wrapper._zf_v3_ia_wrap = True
            globals()[nome_fn] = _wrapper


try:
    _zf_envolver_roteador_chat_v3()
except Exception:
    pass
if st.sidebar.button("📲 Ler e responder Telegram"):
    qtd = sincronizar_e_responder_telegram(processar_prompt)
    if qtd > 0:
        st.sidebar.success(f"{qtd} mensagem(ns) respondida(s) no Telegram!")
    else:
        st.sidebar.info("Nenhuma mensagem nova no Telegram.")
