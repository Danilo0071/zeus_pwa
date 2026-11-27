# ============================================================
# ZEUS 3.0 - app.py (build consolidada 2026-11-27)
# Correções sênior:
#   • __version__ definido (sem AttributeError)
#   • Callers LLM padronizados (bool, str) e _secret unificado
#   • Código órfão removido
#   • Cofre B2 com debounce 15s (não trava)
#   • Auto-aprendizado local (SQLite + SequenceMatcher)
#   • Spotify lateral: capa, clipe, volume ±1, artistas inteiros, voz
#   • 4 avatares: Olho, Esfera, Robô, Zeus (Three.js via CDN)
# ============================================================
__version__ = "3.0.0"

import ast
import asyncio
import base64
import contextlib
import csv as _csv
import hashlib
import html as _html
import importlib
import inspect
import io
import json
import logging
import os
import re
import smtplib as _smtplib
import sqlite3
import ssl as _ssl
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from datetime import datetime as _dtm, timezone as _tzn
from difflib import SequenceMatcher as _SM
from email.message import EmailMessage as _EmailMessage
from email.utils import parseaddr as _parseaddr
from html import unescape as _unescape

import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

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
    salvar_texto = ler_texto = salvar_json = ler_json = None

try:
    import zeus_acoes
    processar_prompt = zeus_acoes.instalar(globals())
except Exception:
    pass

try:
    import zeus_roteador
    zeus_roteador.instalar(globals())
except Exception:
    pass

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
# CONFIGURAÇÃO
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
    for tok, chave in (
        ("__BG__", "bg"),
        ("__TEXT__", "text"),
        ("__PANEL__", "panel"),
        ("__PRIMARY__", "primary"),
        ("__BORDER__", "border"),
    ):
        css = css.replace(tok, t[chave])
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
# LOG + CHAVES + B2 + BANCO
# ============================================================
_log = logging.getLogger("zeus.cofre")
CHAVES_PADRAO = {}


def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", str(v or "")).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v


def _secret(key: str, default: str = "") -> str:
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


def _get_secret(name: str) -> str:
    return _secret(name, "")


DB_FILE = "zeus_chat.db"
COFRE_DB_KEY = "banco/zeus_chat.db"
COFRE_INTERVALO_SEG = 15.0
COFRE_BACKOFF_SEG = 30


@st.cache_resource
def _cofre_ctx():
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
    return endpoint, _secret("B2_BUCKET", "zeus-cofre-danilo"), _secret("B2_KEY_ID"), _secret("B2_APP_KEY")


def _obter_cliente_cofre():
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
    base = f"{type(e).__name__}: {e}"
    return f"{base} [{err.get('Code', '')} - {err.get('Message', '')}]" if (err.get("Code") or err.get("Message")) else base


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


def _conectar():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sessao ON messages(session_id, id)")
    conn.commit()
    return conn


def contar_mensagens() -> int:
    with contextlib.closing(_conectar()) as conn:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def _snapshot_local() -> bytes:
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
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(corpo)
        conn = _conectar()
        try:
            conn.execute("ATTACH DATABASE ? AS nuvem", (tmp,))
            tem = conn.execute("SELECT 1 FROM nuvem.sqlite_master WHERE type='table' AND name='messages'").fetchone()
            if tem:
                conn.execute(
                    """INSERT INTO main.messages (session_id, role, content, timestamp)
                    SELECT n.session_id, n.role, n.content, n.timestamp FROM nuvem.messages n
                    WHERE NOT EXISTS (SELECT 1 FROM main.messages m
                        WHERE m.session_id = n.session_id AND m.role = n.role
                          AND m.content = n.content AND m.timestamp IS n.timestamp) ORDER BY n.id"""
                )
            conn.commit()
            conn.execute("DETACH DATABASE nuvem")
        finally:
            conn.close()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _baixar_corpo_s3(cliente, bucket):
    return cliente.get_object(Bucket=bucket, Key=COFRE_DB_KEY)["Body"].read()


def _enviar_corpo_s3(cliente, bucket, corpo):
    cliente.put_object(Bucket=bucket, Key=COFRE_DB_KEY, Body=corpo, ContentType="application/octet-stream")


def _baixar_corpo_b2_nativo(key_id, app_key, bucket_nome):
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


def _enviar_corpo_b2_nativo(key_id, app_key, bucket_nome, corpo):
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


def _baixar_e_mesclar(cliente, bucket):
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


def _enviar_para_nuvem(cliente, bucket, corpo):
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


def baixar_db_cofre() -> bool:
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


def init_db():
    try:
        if "zeus_init_memoria" in globals():
            zeus_init_memoria()
    except Exception:
        pass
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
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
            conn.commit()
    except Exception as e:
        _log.warning("add_message: %s", e)
        return
    if time.time() - _cofre_ctx()["ultimo_envio"] >= COFRE_INTERVALO_SEG:
        sincronizar_db_cofre()


def get_recent_context(session_id, limit=6):
    try:
        if not _cofre_ctx()["nuvem_ok"]:
            baixar_db_cofre()
        with contextlib.closing(_conectar()) as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []


def diagnostico_cofre():
    itens = []

    def add(n, ok, det=""):
        itens.append((n, bool(ok), det))

    try:
        import boto3

        add("Pacote boto3", True, getattr(boto3, "__version__", "?"))
    except ImportError:
        add("Pacote boto3", False, "não instalado: adicione 'boto3' ao requirements.txt")
    try:
        import b2sdk

        add("Pacote b2sdk", True, getattr(b2sdk, "__version__", "instalado"))
    except ImportError:
        add("Pacote b2sdk", False, "opcional")
    endpoint, bucket, key_id, app_key = _obter_config_cofre()
    add("B2_KEY_ID", key_id, f"{key_id[:6]}… ({len(key_id)} chars)" if key_id else "não encontrado")
    add("B2_APP_KEY", app_key, f"{len(app_key)} chars" if app_key else "não encontrado")
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
                add(f"Arquivo {COFRE_DB_KEY}", tam > 0, f"{tam} bytes" if tam else "0 bytes")
            except Exception as e:
                add(f"Arquivo {COFRE_DB_KEY}", False, "ainda não existe" if _eh_nao_encontrado(e) else _detalhe_erro(e))
    except Exception as e:
        det = _detalhe_erro(e)
        add("Acesso ao bucket (S3)", False, f"{det} — {_dica(det)}")
    add(
        "Banco local",
        os.path.exists(DB_FILE),
        f"{os.path.getsize(DB_FILE)} bytes · {contar_mensagens()} msgs" if os.path.exists(DB_FILE) else "não criado",
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
                f"Envio concluído ({_cofre_ctx()['ultimo_tamanho']} bytes)." if ok else "Envio falhou, veja abaixo."
            )
            for nome, ok_, det in diagnostico_cofre():
                st.write(f"{'✅' if ok_ else '❌'} *{nome}* — {det}")
        else:
            st.caption("Toque no botão para testar a conexão.")


# ============================================================
# COMANDOS DIRETOS NO CHAT PARA O COFRE
# ============================================================
def salvar_arquivo_cofre(nome_arquivo: str, conteudo: str):
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
            cliente.put_object(Bucket=bucket, Key=nome_limpo, Body=dados, ContentType="text/plain; charset=utf-8")
            ctx["modo_ativo"] = "s3"
            return True, f"Arquivo salvo no B2 ({nome_limpo}, {len(dados)} bytes) via S3."
    except Exception as e_s3:
        if not (key_id and app_key):
            return False, f"Falha: {_detalhe_erro(e_s3)}"
    try:
        if not (key_id and app_key):
            return False, "Credenciais ausentes."
        api = _criar_cliente_b2_nativo(key_id, app_key)
        b2b = api.get_bucket_by_name(bucket)
        b2b.upload_bytes(dados, nome_limpo)
        ctx["modo_ativo"] = "b2sdk"
        return True, f"Arquivo salvo no B2 ({nome_limpo}, {len(dados)} bytes) via b2sdk."
    except Exception as e_b2:
        return False, f"Falha: {_detalhe_erro(e_b2)}"


def apagar_arquivo_cofre(nome_arquivo: str):
    nome_limpo = (nome_arquivo or "").strip().lstrip("/")
    if not nome_limpo:
        return False, "Informe o nome do arquivo."
    candidatos = [nome_limpo] + ([f"cofre/{nome_limpo}"] if "/" not in nome_limpo else [])
    cliente, bucket = _obter_cliente_cofre()
    ctx = _cofre_ctx()
    _, _, key_id, app_key = _obter_config_cofre()
    if not bucket:
        return False, "Bucket do B2 não configurado."
    for chave in candidatos:
        try:
            if cliente and ctx.get("modo_ativo") != "b2sdk":
                cliente.delete_object(Bucket=bucket, Key=chave)
                return True, f"Arquivo apagado ({chave})."
        except Exception:
            pass
        try:
            if key_id and app_key:
                api = _criar_cliente_b2_nativo(key_id, app_key)
                b2b = api.get_bucket_by_name(bucket)
                versao = b2b.get_file_info_by_name(chave)
                b2b.delete_file_version(versao.id_, chave)
                return True, f"Arquivo apagado ({chave})."
        except Exception:
            pass
    return False, f"Não foi possível apagar {nome_limpo}."


def listar_arquivos_cofre(limite: int = 20):
    cliente, bucket = _obter_cliente_cofre()
    _, _, key_id, app_key = _obter_config_cofre()
    if not bucket:
        return False, "Bucket do B2 não configurado."
    try:
        if cliente:
            resp = cliente.list_objects_v2(Bucket=bucket, MaxKeys=limite)
            itens = [obj["Key"] for obj in resp.get("Contents", [])]
            return True, ("Bucket vazio." if not itens else "Arquivos no B2:\n- " + "\n- ".join(itens))
    except Exception:
        pass
    try:
        if key_id and app_key:
            api = _criar_cliente_b2_nativo(key_id, app_key)
            b2b = api.get_bucket_by_name(bucket)
            itens = [fv.file_name for fv, _ in b2b.ls(latest_only=True, show_versions=False)][:limite]
            return True, ("Bucket vazio." if not itens else "Arquivos no B2:\n- " + "\n- ".join(itens))
    except Exception as e:
        return False, f"Falha: {_detalhe_erro(e)}"
    return False, "Sem credenciais válidas."


def tratar_comando_cofre_chat(mensagem: str):
    txt = (mensagem or "").strip()
    baixo = txt.lower()
    if baixo.startswith("b2_listar"):
        return listar_arquivos_cofre()[1]
    if baixo.startswith("b2_salvar:"):
        corpo = txt[len("b2_salvar:") :].strip()
        nome, conteudo = corpo.split("|", 1) if "|" in corpo else ("cofre/teste_chat.txt", corpo)
        return salvar_arquivo_cofre(nome.strip(), conteudo.strip())[1]
    if baixo.startswith("b2_apagar:"):
        return apagar_arquivo_cofre(txt[len("b2_apagar:") :].strip())[1]
    return None


# ============================================================
# AUTO-APRENDIZADO LOCAL (SQLite + SequenceMatcher)
# ============================================================
DB_APRENDIZADO = "zeus_aprendizado.db"
_LOCK_APR = threading.RLock()


def _conectar_apr():
    conn = sqlite3.connect(DB_APRENDIZADO, timeout=30, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pares (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta TEXT NOT NULL, resposta TEXT NOT NULL,
        acertos INTEGER DEFAULT 0, criado_em DATETIME DEFAULT CURRENT_TIMESTAMP)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pergunta ON pares(pergunta)")
    conn.commit()
    return conn


def _normalizar_texto(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^\wÀ-ÿ\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def aprender_resposta(pergunta: str, resposta: str) -> bool:
    p, r = (pergunta or "").strip(), (resposta or "").strip()
    if len(p) < 4 or len(r) < 4:
        return False
    with _LOCK_APR:
        try:
            with contextlib.closing(_conectar_apr()) as conn:
                existe = conn.execute("SELECT id FROM pares WHERE pergunta=? AND resposta=? LIMIT 1", (p, r)).fetchone()
                if existe:
                    conn.execute("UPDATE pares SET acertos=acertos+1 WHERE id=?", (existe[0],))
                else:
                    conn.execute("INSERT INTO pares (pergunta, resposta) VALUES (?, ?)", (p, r))
                conn.commit()
            return True
        except Exception:
            return False


def buscar_resposta_local(pergunta: str, limiar: float = 0.82):
    alvo = _normalizar_texto(pergunta)
    if len(alvo) < 4:
        return None, 0.0
    melhor, score_melhor = None, 0.0
    with _LOCK_APR:
        try:
            with contextlib.closing(_conectar_apr()) as conn:
                rows = conn.execute("SELECT pergunta, resposta FROM pares ORDER BY acertos DESC LIMIT 500").fetchall()
        except Exception:
            return None, 0.0
    for p, r in rows:
        s = _SM(None, alvo, _normalizar_texto(p)).ratio()
        if s > score_melhor:
            score_melhor, melhor = s, r
    return (melhor, score_melhor) if score_melhor >= limiar else (None, score_melhor)


def estatisticas_aprendizado() -> dict:
    with _LOCK_APR:
        try:
            with contextlib.closing(_conectar_apr()) as conn:
                total = conn.execute("SELECT COUNT(*) FROM pares").fetchone()[0]
                top = conn.execute("SELECT pergunta, acertos FROM pares ORDER BY acertos DESC LIMIT 5").fetchall()
            return {"total": total, "top": top}
        except Exception:
            return {"total": 0, "top": []}


# ============================================================
# CLIENTE LLM (GROQ, GEMINI, CLAUDE, ...) PADRONIZADO
# ============================================================
SYS_PROMPT = (
    "Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
    "Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
    "Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico quando preciso. "
    "Você evolui continuamente: aprende com outras IAs, com a web e com o usuário. "
    "Quando as IAs falham, você busca na internet por conta própria e responde com o que encontrar. "
    "Você pode enviar mensagens no Telegram e e-mails quando o usuário pedir. "
    "Responda em português. Seja objetivo."
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
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"],
    "claude": ["claude-3-5-haiku-20241022", "claude-haiku-5-5"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    "github": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"],
    "deepinfra": ["deepseek-ai/DeepSeek-V3", "meta-llama/Llama-3.3-70B-Instruct"],
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
        for _attempt in range(2):
            try:
                ok, text = fn(model)
            except Exception as e:
                ok, text = False, f"exceção: {type(e).__name__}: {e}"
            if ok:
                return True, text
            last = str(text)[:200]
            break
        if "credit balance" in last.lower():
            break
    return False, last


def _call_groq(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs,
            "temperature": 0.4,
            "max_tokens": 700,
        },
        timeout=60,
    )
    if r.status_code == 200:
        return True, (r.json()["choices"][0]["message"].get("content") or "").strip()
    return False, r.text[:200]


def _call_claude(api_key, model, msgs, system=None):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs},
        timeout=60,
    )
    if r.status_code == 200:
        return True, "".join(b.get("text", "") for b in r.json()["content"])
    return False, r.text[:200]


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
        return True, "".join(p.get("text", "") for p in parts)
    return False, r.text[:200]


def _call_gemini_auto(api_key, model, msgs, system=None):
    ok, texto = _call_gemini(api_key, model, msgs, system, "v1")
    if not ok:
        ok2, t2 = _call_gemini(api_key, model, msgs, system, "v1beta")
        return (ok2, t2) if ok2 else (ok, texto)
    return ok, texto


def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    r = requests.post(
        url,
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs,
            "temperature": 0.4,
            "max_tokens": 700,
        },
        timeout=60,
    )
    if r.status_code == 200:
        try:
            d = r.json()
            return True, (d["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            return True, (r.text or "").strip()
    return False, r.text[:200]


def _call_huggingface(api_key, model, msgs, system=None):
    chave = (api_key or "").strip() or _secret("HF_TOKEN") or _secret("HUGGINGFACE_API_KEY")
    if not chave:
        return False, "chave HF não encontrada"
    modelo = (model or "").strip() or "Qwen/Qwen2.5-7B-Instruct"
    return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", chave, modelo, msgs, system)


def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat(
        "https://models.github.ai/inference/chat/completions",
        api_key,
        model,
        msgs,
        system,
        {"Accept": "application/json"},
    )


def _call_mistral(api_key, model, msgs, system=None):
    key = (api_key or _secret("MISTRAL_API_KEY") or "").strip()
    if not key:
        return False, "MISTRAL_API_KEY ausente"
    payload = ([{"role": "system", "content": system}] if system else []) + msgs
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model or "mistral-small-latest", "messages": payload},
        timeout=30,
    )
    if r.status_code != 200:
        return False, r.text[:200]
    d = r.json()
    txt = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    return (True, txt) if txt else (False, "vazio")


def _call_cloudflare(api_key, model, msgs, system=None):
    key = api_key or _secret("CLOUDFLARE_API_KEY")
    acc = _secret("CLOUDFLARE_ACCOUNT_ID")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/v1/chat/completions"
    return _call_openai_compat(url, key, model, msgs, system)


def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)


def _call_openrouter(api_key, model, msgs, system=None):
    key = (api_key or _secret("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return False, "OPENROUTER_API_KEY ausente"
    payload = ([{"role": "system", "content": system}] if system else []) + msgs
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://streamlit.io",
            "X-Title": "Zeus 3.0",
        },
        json={"model": model or "openrouter/free", "messages": payload},
        timeout=30,
    )
    if r.status_code != 200:
        err = r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None
        return False, str(err or r.text[:200])
    d = r.json()
    txt = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    return (True, txt) if txt else (False, "vazio")


CALLERS = {
    "groq": _call_groq,
    "gemini": _call_gemini_auto,
    "claude": _call_claude,
    "huggingface": _call_huggingface,
    "github": _call_github,
    "deepinfra": _call_deepinfra,
    "openrouter": _call_openrouter,
    "mistral": _call_mistral,
    "cloudflare": _call_cloudflare,
}


def _resumo(text):
    low = str(text).lower()
    if "credit balance" in low:
        return "sem créditos (Plans & Billing)"
    if "high demand" in low or "unavailable" in low:
        return "alta demanda, tente de novo"
    return str(text)[:120]


def get_llm_response(session_id, prompt, model_choice, system=None, use_history=True):
    resposta_cofre = tratar_comando_cofre_chat(prompt)
    if resposta_cofre is not None:
        return True, resposta_cofre, "cofre_b2", []

    # 1) Memória local primeiro (economiza LLM)
    resposta_local, score = buscar_resposta_local(prompt)
    if resposta_local:
        return True, resposta_local, f"memoria_local ({score:.2f})", []

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
            aprender_resposta(prompt, text or "")
            return True, text or "(resposta vazia)", prov, erros
        erros.append(f"{prov}: {_resumo(text)}")
    return False, "❌ Nenhum modelo respondeu. " + " | ".join(erros), None, erros

# ============================================================
# ZEUS TELEGRAM (envio + leitura/resposta)
# ============================================================
def _obter_credenciais_telegram():
    token, chat_id = "", ""
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
    msg_limpa = (texto or "").strip() or "Teste de telemetria."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": destino, "text": f"🚨 [ZEUS] 🚨\n\nCriador: Danilo\n\n{msg_limpa[:3800]}", "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200 and r.json().get("ok"):
            return "✅ SUCESSO: Mensagem enviada ao Telegram."
        return f"❌ FALHA NA API DO TELEGRAM (Status {r.status_code}): {r.text[:200]}"
    except Exception as e:
        return f"❌ EXCEÇÃO DE REDE NO TELEGRAM: {str(e)}"


def _rt_comando_telegram(texto: str) -> str:
    t = re.sub(
        r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*",
        "",
        texto,
    ).strip(" .,!?:-")
    if not t:
        t = "Alerta de telemetria disparado pelo criador Danilo."
    return zf_enviar_telegram(t)


def interceptar_comando_autonomo_telegram(texto: str):
    low = (texto or "").lower()
    if any(k in low for k in ("telegram", "tg", "bunker", "telemetria")):
        return True, _rt_comando_telegram(texto)
    return False, ""


def sincronizar_e_responder_telegram(responder_fn=None) -> int:
    token, chat_id_autorizado = _obter_credenciais_telegram()
    if not token:
        st.sidebar.error("Token do Telegram não encontrado nos Secrets.")
        return 0
    try:
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
    except Exception:
        pass
    if "tg_last_update_id" not in st.session_state:
        st.session_state.tg_last_update_id = 0
    params = {"timeout": 2}
    if st.session_state.tg_last_update_id > 0:
        params["offset"] = int(st.session_state.tg_last_update_id) + 1
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=15)
        dados = r.json()
    except Exception as e:
        st.sidebar.error(f"Erro de rede ao ler Telegram: {e}")
        return 0
    if not dados.get("ok"):
        st.sidebar.error(f"Erro API Telegram: {dados}")
        return 0
    updates = dados.get("result", [])
    if not updates:
        return 0
    respondidas = 0
    for upd in updates:
        uid = int(upd.get("update_id", 0))
        if uid > st.session_state.tg_last_update_id:
            st.session_state.tg_last_update_id = uid
        msg = upd.get("message") or upd.get("edited_message") or {}
        texto = (msg.get("text") or "").strip()
        cid = str((msg.get("chat") or {}).get("id") or "").strip()
        if not texto or not cid:
            continue
        if chat_id_autorizado and cid != str(chat_id_autorizado).strip():
            st.sidebar.warning(f"Ignorado chat_id {cid} (autorizado: {chat_id_autorizado})")
            continue
        if texto == "/start":
            resposta_zeus = "⚡ Zeus ativo no Telegram."
        elif callable(responder_fn):
            resposta_zeus = str(responder_fn(texto) or "Sem resposta.")
        else:
            resposta_zeus = f"Recebido: {texto}"
        retorno = zf_enviar_telegram(resposta_zeus, chat_id_destino=cid)
        if "❌" in str(retorno):
            st.sidebar.error(str(retorno))
        else:
            respondidas += 1
    return respondidas


# ============================================================
# BUSCA NA WEB
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
    "por favor", "pfv", "pf", "me diga", "me diz", "me fale", "me fala", "você pode", "voce pode",
    "poderia", "pode", "gostaria", "queria", "quero", "pesquise", "pesquisa", "pesquisar", "busque",
    "busca", "buscar", "procure", "procura", "procurar", "verifique", "verifica", "confira", "consulte",
    "consulta", "google", "internet", "na web", "na internet", "no google",
)

_STOP_PALAVRAS = {
    "por favor", "pfv", "pf", "zeus", "ei", "olá", "ola", "oi", "bom dia", "boa tarde",
    "boa noite", "me", "mim", "vc", "voce", "você", "para", "pra", "que", "qual", "quais", "sobre", "aqui",
    "agora", "por gentileza", "obrigado", "obrigada", "grato", "faco", "faço", "fazer", "saber", "dizer",
    "falar", "trazer", "traga", "mostre", "mande", "envie", "fonte", "fontes", "link", "links",
}

_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+", flags=re.UNICODE)


def _limpar_query(texto, max_len=160):
    if not texto:
        return ""
    q = _RE_EMOJI.sub(" ", _RE_URL.sub(" ", str(texto)))
    q = re.sub(r"\s+", " ", q).strip()
    low = q.lower()
    mudou = True
    while mudou:
        mudou = False
        for p in _STOP_PREFIXOS:
            if low.startswith(p + " ") or low == p:
                q = q[len(p) :].lstrip(" ,.:;!?-")
                low = q.lower()
                mudou = True
    tokens = [t for t in q.split() if t.lower().strip(".,;:!?") not in _STOP_PALAVRAS]
    return " ".join(tokens).strip(" ,.;:!?-")[:max_len]


def _tokens_relevantes(texto, min_len=3):
    if not texto:
        return set()
    t = re.sub(r"[^\wÀ-ÿ\s]", " ", str(texto).lower())
    return {w for w in t.split() if len(w) >= min_len and w not in _STOP_PALAVRAS}


def _resultado_no_tema(query_limpa, resultado, limiar=0.30):
    q_tokens, r_tokens = _tokens_relevantes(query_limpa), _tokens_relevantes(resultado)
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
        "que", "qual", "quais", "quando", "onde", "como", "porque", "por", "para", "pra", "de", "do", "da", "dos",
        "das", "o", "a", "os", "as", "um", "uma", "é", "eh", "foi", "será", "sera", "está", "esta", "tem", "há", "ha", "hoje", "agora",
    }
    toks = [t for t in q.split() if t.lower().strip(".,;:!?") not in ruido]
    base = " ".join(toks).strip()[:110]
    if base and not re.search(r"\b20\d{2}\b", base):
        return f"{base} {time.strftime('%Y')}"[:120]
    return base


def buscar_web_tavily(query: str, max_results: int = 5) -> str:
    api_key = ((st.secrets.get("TAVILY_API_KEY") if hasattr(st, "secrets") else "") or os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return ""
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "search_depth": "basic", "include_answer": True, "max_results": int(max_results or 5)},
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


def _search_google(q, n=5):
    linhas = []
    try:
        from googlesearch import search as google_search

        for r in google_search(q, num_results=int(n or 5), lang="pt", region="br", advanced=True, sleep_interval=0.5, timeout=10):
            titulo = (getattr(r, "title", "") or "").strip()
            url = (getattr(r, "url", "") or "").strip()
            desc = (getattr(r, "description", "") or "").strip()
            if titulo and url:
                linhas.append(f"- {titulo}: {desc} ({url})")
    except Exception:
        pass
    return "\n".join(linhas)


def _search_multi(q, n=5):
    linhas = []
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            for r in ddgs.text(q, region="br-pt", safesearch="moderate", max_results=int(n or 5)) or []:
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
            params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": int(n or 4)},
            headers={"User-Agent": "ZeusBot/4.0"},
            timeout=10,
        )
        if r.status_code != 200:
            return ""
        itens = r.json().get("query", {}).get("search", [])
        return "\n".join(
            f"- {i.get('title','').strip()}: {re.sub(r'<[^>]+>', '', i.get('snippet','')).strip()} (https://pt.wikipedia.org/wiki/{i.get('title','').replace(' ', '_')})"
            for i in itens
            if i.get("title")
        )
    except Exception:
        return ""


_FONTES_WEB = (("Google", _search_google), ("Bing/DuckDuckGo/Yahoo", _search_multi), ("Wikipédia", _search_wiki))


def _executar_fontes(q, n, limiar=0.20):
    coletados, fontes = [], []
    for nome, fn in _FONTES_WEB:
        try:
            txt = fn(q, n)
        except Exception:
            txt = ""
        txt = (txt or "").strip()
        if not txt:
            continue
        if "Wikipédia" in nome:
            try:
                if not _resultado_no_tema(q, txt, limiar=limiar):
                    continue
            except Exception:
                pass
        coletados.append(f"[{nome}]\n{txt}")
        fontes.append(nome)
        if len("\n\n".join(coletados)) >= 1200:
            break
    return "\n\n".join(coletados), ", ".join(fontes)


def web_search(q, n=5):
    q_limpa = _limpar_query(q) if "_limpar_query" in globals() else (q or "").strip()
    if not q_limpa:
        return "", ""
    r = buscar_web_tavily(q_limpa, max_results=n)
    if r and r.strip():
        return r.strip(), "Tavily"
    txt, fonte = _executar_fontes(q_limpa, n, limiar=0.20)
    if txt:
        return txt, fonte
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
    return any(t in low for t in WEB_TRIGGERS) or bool(re.search(r"\b20(2[4-9]|3[0-9])\b", low)) or len(low.split()) <= 5


_REGRAS_WEB_LIVRE = (
    "INSTRUÇÕES DE RESPOSTA:\n"
    "1. Priorize fatos de [DADOS DA INTERNET] quando houver.\n"
    "2. Se vazio/incompleto, responda livremente com seu conhecimento, sem dizer que falhou.\n"
    "3. Nunca invente URLs; cite só as que aparecerem.\n"
)


def enriquecer_com_web(prompt):
    hoje = time.strftime("%d/%m/%Y")
    if not precisa_web(prompt):
        return prompt, "llm_direto"
    ctx_web, fonte = web_search(prompt)
    if not ctx_web:
        return (
            f"[CONTEXTO WEB em {hoje}: sem resultados diretos]\n{_REGRAS_WEB_LIVRE}\nResponda da melhor forma possível.\nPergunta: {prompt}"
        ), "fallback_llm"
    return (
        f"[DADOS DA INTERNET em {hoje}, via {fonte}]\n{ctx_web[:4500]}\n[FIM]\n\n{_REGRAS_WEB_LIVRE}\nPergunta: {prompt}"
    ), fonte


# ============================================================
# GERAÇÃO DE MÍDIA
# ============================================================
def _mime_img(b):
    return "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"


def _to_bytes(obj):
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    buf = io.BytesIO()
    obj.save(buf, format="PNG")
    return buf.getvalue()


def _hf_client(timeout=120):
    from huggingface_hub import InferenceClient

    return InferenceClient(api_key=_secret("HUGGINGFACE_API_KEY"), timeout=timeout)


def _img_hf(prompt):
    if not _secret("HUGGINGFACE_API_KEY"):
        raise RuntimeError("sem chave")
    modelos = [m for m in [_secret("HF_IMAGE_MODEL")] if m] + [
        "black-forest-labs/FLUX.1-schnell",
        "stabilityai/stable-diffusion-xl-base-1.0",
    ]
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
    raise RuntimeError("Gemini não devolveu imagem")


def _img_gemini(prompt):
    return _gemini_image_call([{"text": prompt}])


def _img_pollinations(prompt):
    from urllib.parse import quote

    ultimo = ""
    for tentativa in range(2):
        r = requests.get(
            "https://image.pollinations.ai/prompt/" + quote(prompt[:600]),
            params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=120,
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
    r = requests.post(
        "https://api.deepinfra.com/v1/openai/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"prompt": prompt, "size": "1024x1024", "n": 1, "model": modelo},
        timeout=120,
    )
    if r.status_code == 200:
        return base64.b64decode(r.json()["data"][0]["b64_json"])
    raise RuntimeError(f"REST ({r.status_code}) {r.text[:60]}")


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
    r = requests.post(
        OPENROUTER_BASE + "/chat/completions",
        headers=_or_headers(key),
        json={"model": modelo, "messages": [{"role": "user", "content": prompt}], "modalities": ["image", "text"]},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    return _or_extrair_imagem(r.json())


def _edit_gemini(img, prompt):
    return _gemini_image_call(
        [{"text": prompt}, {"inline_data": {"mime_type": _mime_img(img), "data": base64.b64encode(img).decode()}}]
    )


IMAGE_CASCADE = [
    ("DeepInfra", _img_deepinfra),
    ("OpenRouter", _img_openrouter),
    ("Hugging Face", _img_hf),
    ("Gemini", _img_gemini),
    ("Pollinations", _img_pollinations),
]
EDIT_CASCADE = [("Gemini", _edit_gemini)]


def make_image(prompt, log):
    for nome, fn in IMAGE_CASCADE:
        try:
            data = fn(prompt)
            log.append("✅ Imagem criada por: " + nome)
            return data
        except Exception as e:
            log.append("❌ Imagem " + nome + ": " + str(e)[:160])
    return None


def editar_imagem(img, prompt_edicao, descricao, log):
    for nome, fn in EDIT_CASCADE:
        try:
            out = fn(img, prompt_edicao)
            log.append("✅ Foto editada por: " + nome)
            return out, "editada"
        except Exception as e:
            log.append("❌ " + nome + ": " + str(e)[:160])
    out = make_image((descricao + ". " + prompt_edicao)[:900], log)
    return out, ("recriada" if out else "")


def make_video(prompt, log, img=None):
    if not _secret("HUGGINGFACE_API_KEY"):
        log.append("⚪ Vídeo: sem HUGGINGFACE_API_KEY")
        return None
    try:
        client = _hf_client(600)
    except ImportError:
        log.append("❌ Vídeo: falta huggingface_hub")
        return None
    try:
        model = _secret("HF_VIDEO_MODEL") or "Wan-AI/Wan2.1-T2V-1.3B"
        out = client.text_to_video(prompt, model=model)
        log.append("✅ Vídeo (texto→vídeo): " + model)
        return _to_bytes(out)
    except Exception as e:
        log.append("❌ Vídeo: " + str(e)[:160])
        return None


# ============================================================
# PIPELINE / VISÃO / ESTÚDIO
# ============================================================
def _llm(prompt, system, log, etapa):
    ok, texto, prov, erros = get_llm_response(
        st.session_state.session_id,
        prompt,
        st.session_state.get("model_sel", "groq"),
        system=system,
        use_history=False,
    )
    if ok:
        log.append("✅ " + etapa + ": " + str(prov) + (" (fallback: " + "; ".join(erros) + ")" if erros else ""))
        return texto
    log.append("❌ " + etapa + ": " + texto[:150])
    return ""


def run_pipeline(pedido, quer_img, quer_vid):
    log = []
    contexto, fonte = web_search(pedido)
    log.append("🔎 Busca (" + fonte + "): ok" if contexto else "⚪ Busca: sem resultados")
    resumo = ""
    if contexto:
        resumo = _llm(
            "Pedido: " + pedido + "\n\nContexto:\n" + contexto[:4000],
            "Você é um pesquisador. Resuma em português, em até 8 linhas, os fatos úteis do contexto.",
            log,
            "Resumo",
        )
    sys_b = (
        "Você é engenheiro de prompts para IAs de mídia. Responda EXATAMENTE em 2 linhas:\n"
        "IMAGEM: <prompt detalhado em inglês>\nVIDEO: <prompt em inglês descrevendo ação e movimento>"
    )
    bruto = _llm("Pedido: " + pedido + "\n\nFatos:\n" + resumo, sys_b, log, "Prompt otimizado")
    m_img = re.search(r"IMAGEM:\s*(.+)", bruto or "")
    m_vid = re.search(r"VIDEO:\s*(.+)", bruto or "")
    pimg = m_img.group(1).strip() if m_img else pedido
    pvid = m_vid.group(1).strip() if m_vid else pimg
    img = make_image(pimg, log) if quer_img else None
    vid = make_video(pvid, log) if quer_vid else None
    return {"log": log, "resumo": resumo, "prompt_img": pimg, "prompt_vid": pvid, "img": img, "vid": vid}


def render_pipeline_ui():
    with st.expander("🔗 Pipeline Multi-IA", expanded=False):
        pedido = st.text_area("O que criar?", key="pipe_req", placeholder="Ex.: um dragão de cristal sobre uma cidade futurista")
        c1, c2 = st.columns(2)
        qi = c1.checkbox("🖼️ Imagem", value=True, key="pipe_img")
        qv = c2.checkbox("🎬 Vídeo", value=False, key="pipe_vid")
        if st.button("🚀 Executar", key="pipe_run") and pedido.strip():
            with st.spinner("Encadeando IAs..."):
                st.session_state.pipe_res = run_pipeline(pedido.strip(), qi, qv)
        res = st.session_state.get("pipe_res")
        if res:
            if res["resumo"]:
                st.markdown("**Resumo**")
                st.write(res["resumo"])
            st.markdown("**Prompt imagem**")
            st.code(res["prompt_img"], language="text")
            if res["img"]:
                st.image(res["img"], caption="Imagem gerada")
            if res["vid"]:
                st.video(res["vid"])
            with st.expander("Log", expanded=not (res["img"] or res["vid"])):
                st.write("\n\n".join(res["log"]))


def _vis_openai(url, key, model, img_b64, mime, prompt, extra=None):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    r = requests.post(
        url,
        headers=headers,
        json={
            "model": model,
            "max_tokens": 700,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + img_b64}},
                    ],
                }
            ],
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _gemini_generate(key, payload, modelos=None):
    modelos = modelos or ([x for x in [_secret("GEMINI_VISION_MODEL")] if x] + ["gemini-2.5-flash", "gemini-flash-latest"])
    ultimo = ""
    for model in modelos:
        for ver in ("v1", "v1beta"):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                json=payload,
                timeout=90,
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
        return _gemini_generate(
            key, {"contents": [{"role": "user", "parts": [{"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]}
        )
    except Exception as e:
        return "❌ Erro: " + str(e)[:200]


def _vis_gemini(k, b, m, p):
    return _gemini_generate(k, {"contents": [{"role": "user", "parts": [{"text": p}, {"inline_data": {"mime_type": m, "data": b}}]}]})


def _vis_groq(k, b, m, p):
    return _vis_openai("https://api.groq.com/openai/v1/chat/completions", k, "meta-llama/llama-4-scout-17b-16e-instruct", b, m, p)


def _vis_claude(k, b, m, p):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": k, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={
            "model": _secret("ANTHROPIC_MODEL") or "claude-haiku-5-5",
            "max_tokens": 900,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": m, "data": b}},
                        {"type": "text", "text": p},
                    ],
                }
            ],
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return "".join(x.get("text", "") for x in r.json()["content"]).strip()


def _vis_hf(k, b, m, p):
    return _vis_openai(
        "https://router.huggingface.co/v1/chat/completions", k, _secret("HF_VISION_MODEL") or "Qwen/Qwen2.5-VL-7B-Instruct", b, m, p
    )


def _vis_github(k, b, m, p):
    return _vis_openai(
        "https://models.github.ai/inference/chat/completions",
        k,
        _secret("GITHUB_VISION_MODEL") or "openai/gpt-4.1-mini",
        b,
        m,
        p,
        {"Accept": "application/json"},
    )


def _vis_deepinfra(k, b, m, p):
    return _vis_openai(
        "https://api.deepinfra.com/v1/openai/chat/completions",
        k,
        _secret("DEEPINFRA_VISION_MODEL") or "Qwen/Qwen2.5-VL-32B-Instruct",
        b,
        m,
        p,
    )


def _vis_openrouter(k, b, m, p):
    return _vis_openai(
        OPENROUTER_BASE + "/chat/completions",
        k,
        _secret("OPENROUTER_VISION_MODEL") or "google/gemini-2.5-flash",
        b,
        m,
        p,
        {"X-Title": "Zeus 3.0"},
    )


VISION_CASCADE = {
    "deepinfra": _vis_deepinfra,
    "openrouter": _vis_openrouter,
    "groq": _vis_groq,
    "gemini": _vis_gemini,
    "claude": _vis_claude,
    "huggingface": _vis_hf,
    "github": _vis_github,
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
                log.append("✅ Visão: " + prov)
                return txt, prov, log
        except Exception as e:
            log.append("❌ Visão " + prov + ": " + str(e)[:120])
    return None, None, log


ACOES = ["Descrever", "Editar foto", "Criar vídeo", "Editar foto + vídeo"]
ESTILOS_FOTO = {
    "Automático": "",
    "Realista HD": "ultra realistic, sharp details, professional photography",
    "Cyberpunk neon": "cyberpunk neon lights, magenta and cyan rim light",
    "Anime": "anime style, vibrant colors, clean line art",
    "Pintura a óleo": "classic oil painting, visible brush strokes",
    "Preto e branco cinematográfico": "cinematic black and white, high contrast, film grain",
    "Fantasia épica": "epic fantasy art, dramatic lighting",
    "Cartoon 3D": "3D cartoon render, expressive lighting, clean stylized shading",
}

# ============================================================
# AVATARES 3D (OLHO, ESFERA, ROBÔ, ZEUS THREE.JS) + ÁUDIO / VOZ
# ============================================================
def _zf_gerar_audio(texto, voz_id=""):
    texto_curto = re.sub(r"\s+", " ", str(texto or "")).strip()[:500]
    if not texto_curto:
        return None, None
    try:
        from io import BytesIO
        from gtts import gTTS

        buf = BytesIO()
        gTTS(text=texto_curto, lang="pt").write_to_fp(buf)
        buf.seek(0)
        dados = buf.read()
        if dados:
            return dados, "audio/mpeg"
    except Exception:
        pass
    return None, None


def _zf_falar_resposta(texto, voz_id, chave_id=""):
    chave_limpa = re.sub(r"[^a-zA-Z0-9-]", "", str(chave_id or "resp")) or "resp"
    audio_bytes, mime = _zf_gerar_audio(texto, voz_id)
    texto_js = (
        str(texto or "")[:700]
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )

    if audio_bytes and mime:
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        components.html(
            f"""
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:#0e1525;border:1px solid #1f3354;border-radius:10px;padding:6px 10px;color:#cfe6ff;font-family:sans-serif;font-size:12px;">
              <audio id="zeus-player-{chave_limpa}" controls playsinline preload="auto" style="height:30px;flex:1;min-width:160px;">
                <source src="data:{mime};base64,{b64}" type="{mime}">
              </audio>
              <button id="zeus-playbtn-{chave_limpa}" onclick="const a=document.getElementById('zeus-player-{chave_limpa}');if(a){{a.currentTime=0;a.play().catch(()=>{{}});}}"
                      style="background:#143628;color:#80ed99;border:1px solid #2d6a4f;border-radius:6px;padding:5px 9px;cursor:pointer;font-weight:600;">
                Falar
              </button>
              <button onclick="const a=document.getElementById('zeus-player-{chave_limpa}');if(a){{a.pause();a.currentTime=0;}}if('speechSynthesis' in window){{window.speechSynthesis.cancel();}}"
                      style="background:#2b1620;color:#ff8fa3;border:1px solid #6b263c;border-radius:6px;padding:5px 8px;cursor:pointer;">
                Parar
              </button>
            </div>
            <script>
            (function() {{
                try {{
                    const el = document.getElementById('zeus-player-{chave_limpa}');
                    if (window._zeusAudioAtual && window._zeusAudioAtual !== el) {{
                        try {{ window._zeusAudioAtual.pause(); }} catch(e){{}}
                    }}
                    window._zeusAudioAtual = el;
                    if (el) {{
                        const p = el.play();
                        if (p && typeof p.catch === 'function') {{
                            p.catch(() => {{
                                const btn = document.getElementById('zeus-playbtn-{chave_limpa}');
                                if (btn) btn.style.boxShadow = '0 0 0 2px #ffd166';
                            }});
                        }}
                    }}
                }} catch (e) {{}}
            }})();
            </script>
            """,
            height=52,
        )
        return

    components.html(
        f"""
        <div style="display:flex;align-items:center;gap:8px;background:#0e1525;border:1px solid #1f3354;border-radius:10px;padding:6px 10px;color:#cfe6ff;font-family:sans-serif;font-size:12px;">
          <span style="flex:1;">Resposta pronta para falar</span>
          <button onclick="if('speechSynthesis' in window){{window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(\"{texto_js}\");u.lang='pt-BR';u.rate=1.0;window.speechSynthesis.speak(u);}}"
                  style="background:#143628;color:#80ed99;border:1px solid #2d6a4f;border-radius:6px;padding:5px 9px;cursor:pointer;font-weight:600;">
            Falar
          </button>
        </div>
        <script>
        (function() {{
            if (!('speechSynthesis' in window)) return;
            try {{
                window.speechSynthesis.cancel();
                const u = new SpeechSynthesisUtterance("{texto_js}");
                u.lang = 'pt-BR';
                u.rate = 1.0;
                window.speechSynthesis.speak(u);
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=44,
    )


def avatar_zeus_3d_html():
    """Avatar 3D realista de Zeus mitologico com raios nas maos (Three.js)."""
    return """<!DOCTYPE html>
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

const torsoMat = new THREE.MeshStandardMaterial({color:0xc4a574, metalness:0.15, roughness:0.55});
const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.7, 1.3, 16), torsoMat);
torso.position.y = 0.9;
group.add(torso);

const head = new THREE.Mesh(new THREE.SphereGeometry(0.42, 24, 24), torsoMat);
head.position.y = 1.85;
group.add(head);

const hairMat = new THREE.MeshStandardMaterial({color:0xf0f0f5, metalness:0.05, roughness:0.7});
const hair = new THREE.Mesh(new THREE.SphereGeometry(0.48, 16, 16), hairMat);
hair.position.y = 2.05;
hair.scale.y = 0.7;
group.add(hair);
const beard = new THREE.Mesh(new THREE.ConeGeometry(0.38, 0.55, 12), hairMat);
beard.position.set(0, 1.55, 0.28);
beard.rotation.x = Math.PI;
group.add(beard);

const eyeMat = new THREE.MeshBasicMaterial({color:0x00eeff});
const le = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 12), eyeMat);
le.position.set(-0.14, 1.88, 0.36);
const re = le.clone(); re.position.x = 0.14;
group.add(le, re);

const armMat = new THREE.MeshStandardMaterial({color:0xb8956a, metalness:0.1, roughness:0.6});
const armL = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 1.1, 10), armMat);
armL.position.set(-0.85, 1.15, 0.1);
armL.rotation.z = 0.55;
const armR = armL.clone();
armR.position.set(0.85, 1.15, 0.1);
armR.rotation.z = -0.55;
group.add(armL, armR);

function makeBolt(x, y, z, rotZ) {
  const boltGroup = new THREE.Group();
  const boltMat = new THREE.MeshBasicMaterial({color:0xffee55, transparent:true, opacity:0.95});
  const core = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.02, 1.3, 6), boltMat);
  core.position.y = 0.65;
  boltGroup.add(core);
  for (let i=0; i<4; i++) {
    const branch = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.01, 0.45, 5), boltMat);
    branch.position.set((i%2===0?0.12:-0.12), 0.4 + i*0.22, 0);
    branch.rotation.z = (i%2===0?0.6:-0.6);
    boltGroup.add(branch);
  }
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
</html>"""


def _zf_widget_escuta_continua(ativo: bool, segundos_silencio: float = 2.0):
    """Escuta continua adaptada para PC e celular (envia apos 2s de pausa e interrompe fala ao detectar voz)."""
    if not ativo:
        return

    ms_silencio = int(segundos_silencio * 1000)
    components.html(
        f"""
        <div id="zf-live-box" style="background:#0b1324;border:1px solid #1e3a5f;border-radius:10px;padding:10px 12px;color:#e6f1ff;font-family:sans-serif;font-size:13px;">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
            <span id="zf-live-status" style="font-weight:600;color:#4cc9f0;">Ouvindo... fale normalmente (envia apos {segundos_silencio:.0f}s de pausa)</span>
            <span id="zf-live-timer" style="font-size:11px;color:#8da9c4;">aguardando voz</span>
          </div>
          <div id="zf-live-preview" style="margin-top:6px;min-height:20px;color:#b8d4f0;font-style:italic;">...</div>
        </div>
        <script>
        (function() {{
            const statusEl = document.getElementById('zf-live-status');
            const timerEl = document.getElementById('zf-live-timer');
            const prevEl = document.getElementById('zf-live-preview');
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || '');

            if (!SR) {{
                statusEl.textContent = 'Este navegador nao suporta escuta automatica. Abra o microfone manual abaixo.';
                statusEl.style.color = '#ffb703';
                return;
            }}

            function interromperZeusFalando() {{
                try {{
                    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
                    if (window._zeusAudioAtual) {{
                        try {{ window._zeusAudioAtual.pause(); }} catch(e){{}}
                    }}
                    document.querySelectorAll('audio').forEach(a => {{ try {{ a.pause(); }} catch(e){{}} }});
                }} catch (e) {{}}
            }}

            function enviarParaStreamlit(textoFinal) {{
                const limpo = (textoFinal || '').trim();
                if (!limpo) return;
                try {{
                    const doc = (window.parent && window.parent.document) ? window.parent.document : document;
                    const inp = doc.querySelector('input[aria-label="zf_voz_live_bridge"]');
                    if (!inp) return;
                    const win = (window.parent && window.parent.HTMLInputElement) ? window.parent : window;
                    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, limpo + '||' + Date.now());
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));
                }} catch (e) {{}}
            }}

            const rec = new SR();
            rec.lang = 'pt-BR';
            rec.continuous = !isMobile;
            rec.interimResults = true;
            rec.maxAlternatives = 1;

            let textoFinalAcumulado = '';
            let ultimoParcial = '';
            let silencioTimeout = null;
            let contadorInterval = null;
            let enviado = false;

            function montarTextoAtual() {{
                const base = textoFinalAcumulado.trim();
                const parc = ultimoParcial.trim();
                if (base && parc && !base.toLowerCase().endsWith(parc.toLowerCase())) {{
                    return (base + ' ' + parc).trim();
                }}
                return (base || parc).trim();
            }}

            function despacharAgora() {{
                clearTimeout(silencioTimeout);
                clearInterval(contadorInterval);
                const finalTxt = montarTextoAtual();
                if (!enviado && finalTxt.length > 0) {{
                    enviado = true;
                    statusEl.textContent = 'Enviando fala para o Zeus...';
                    statusEl.style.color = '#80ed99';
                    timerEl.textContent = 'processando';
                    try {{ rec.stop(); }} catch(e){{}}
                    enviarParaStreamlit(finalTxt);
                }}
            }}

            function reiniciarTimerSilencio() {{
                clearTimeout(silencioTimeout);
                clearInterval(contadorInterval);
                let restante = {ms_silencio};
                timerEl.textContent = 'enviando em ' + (restante / 1000).toFixed(1) + 's...';
                contadorInterval = setInterval(() => {{
                    restante -= 200;
                    if (restante > 0) {{
                        timerEl.textContent = 'enviando em ' + (restante / 1000).toFixed(1) + 's...';
                    }} else {{
                        clearInterval(contadorInterval);
                    }}
                }}, 200);

                silencioTimeout = setTimeout(despacharAgora, {ms_silencio});
            }}

            rec.onresult = function(ev) {{
                interromperZeusFalando();
                let novoFinal = '';
                let novoParcial = '';
                for (let i = ev.resultIndex; i < ev.results.length; i++) {{
                    const parte = (ev.results[i][0] && ev.results[i][0].transcript) || '';
                    if (ev.results[i].isFinal) {{
                        novoFinal += ' ' + parte;
                    }} else {{
                        novoParcial += ' ' + parte;
                    }}
                }}
                if (novoFinal.trim()) {{
                    textoFinalAcumulado = (textoFinalAcumulado + ' ' + novoFinal.trim()).trim();
                    ultimoParcial = '';
                }} else {{
                    ultimoParcial = novoParcial.trim();
                }}

                const exibicao = montarTextoAtual();
                if (exibicao) {{
                    statusEl.textContent = 'Captando sua voz...';
                    statusEl.style.color = '#ffd166';
                    prevEl.textContent = exibicao;
                    reiniciarTimerSilencio();
                }}
            }};

            rec.onerror = function(ev) {{
                if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {{
                    statusEl.textContent = 'Permissao de microfone bloqueada. Use o microfone manual abaixo.';
                    statusEl.style.color = '#ff6b6b';
                }} else if (ev.error === 'no-speech' && montarTextoAtual()) {{
                    despacharAgora();
                }}
            }};

            rec.onend = function() {{
                if (enviado) return;
                if (montarTextoAtual()) {{
                    reiniciarTimerSilencio();
                }}
                setTimeout(() => {{
                    if (!enviado) {{
                        try {{ rec.start(); }} catch(e) {{}}
                    }}
                }}, isMobile ? 250 : 120);
            }};

            try {{
                rec.start();
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=76,
    )


# ============================================================
# INTERFACE PRINCIPAL STREAMLIT
# ============================================================
def render_app():
    init_db()
    if "session_id" not in st.session_state:
        st.session_state.session_id = "sessao_principal"
    if "theme" not in st.session_state:
        st.session_state.theme = "Cyberpunk"
    if "model_sel" not in st.session_state:
        st.session_state.model_sel = "groq"
    if "web_mode" not in st.session_state:
        st.session_state.web_mode = "Automática"

    inject_theme(st.session_state.theme)

    with st.sidebar:
        st.title("⚡ ZEUS 3.0")
        st.caption(f"Build {__version__}")
        st.session_state.model_sel = st.selectbox(
            "Provedor LLM",
            PROVIDERS,
            index=PROVIDERS.index(st.session_state.model_sel) if st.session_state.model_sel in PROVIDERS else 0,
        )
        st.session_state.web_mode = st.selectbox(
            "Busca na Web",
            ["Automática", "Sempre", "Desligada"],
            index=["Automática", "Sempre", "Desligada"].index(st.session_state.web_mode),
        )
        escuta_ativa = st.toggle("🎙️ Escuta contínua", value=False, key="zf_escuta_live")
        if st.button("📡 Sincronizar Telegram", use_container_width=True):
            qtd = sincronizar_e_responder_telegram()
            st.success(f"Mensagens respondidas: {qtd}")
        mostrar_diagnostico_cofre()

    st.markdown("## ⚡ ZEUS 3.0 — NÚCLEO ATIVO")
    col_chat, col_3d = st.columns([2, 1])

    with col_3d:
        st.markdown("### 🔱 Avatar 3D")
        components.html(avatar_zeus_3d_html(), height=320)
        _zf_widget_escuta_continua(escuta_ativa)

    with col_chat:
        render_pipeline_ui()
        historico = get_recent_context(st.session_state.session_id, limit=20)
        for idx, msg in enumerate(historico):
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                if msg["role"] == "assistant":
                    _zf_falar_resposta(msg["content"], "", chave_id=f"hist_{idx}")

        prompt_live = st.text_input("zf_voz_live_bridge", key="zf_voz_live_input", label_visibility="collapsed", placeholder="")
        prompt = st.chat_input("Fale com o Zeus...") or (prompt_live.split("||")[0] if prompt_live else "")

        if prompt and prompt.strip():
            texto_usuario = prompt.strip()
            add_message(st.session_state.session_id, "user", texto_usuario)
            with st.chat_message("user"):
                st.write(texto_usuario)

            ok_tg, resp_tg = interceptar_comando_autonomo_telegram(texto_usuario)
            if ok_tg:
                resposta_final = resp_tg
            else:
                prompt_enriquecido, _ = enriquecer_com_web(texto_usuario)
                _, resposta_final, _, _ = get_llm_response(
                    st.session_state.session_id,
                    prompt_enriquecido,
                    st.session_state.model_sel,
                )

            add_message(st.session_state.session_id, "assistant", resposta_final)
            with st.chat_message("assistant"):
                st.write(resposta_final)
                _zf_falar_resposta(resposta_final, "", chave_id=f"live_{len(historico)}")


if __name__ == "__main__":
    render_app()
