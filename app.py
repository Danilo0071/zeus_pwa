# ============================================================
# ZEUS 3.0 - app.py (build 2026-11-27) — PARTE 1
# Imports, config, B2, banco, auto-aprendizado, LLM
# ============================================================
import ast
import asyncio
import base64
import contextlib
import hashlib
import importlib
import inspect
import io
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher as _SM
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

# Módulos opcionais do Zeus (se existirem no seu projeto)
for _mod in ("zeus_acoes", "zeus_roteador", "zeus_agente", "zeus_lab"):
    try:
        _m = importlib.import_module(_mod)
        if hasattr(_m, "instalar"):
            _m.instalar(globals())
    except Exception:
        pass

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(page_title="Zeus 3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

THEMES = {"Cyberpunk": {"bg": "#0b0f19", "panel": "#121826", "primary": "#00ffcc", "secondary": "#0077ff", "text": "#00ffcc", "border": "#00ffcc33"}}

def inject_theme(theme_name: str = "Cyberpunk"):
    t = THEMES.get(theme_name, THEMES["Cyberpunk"])
    st.markdown(f"""
        <style>
            .stApp {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Courier New', monospace; }}
            [data-testid="stSidebar"] {{ background-color: {t['panel']}; }}
            h1, h2, h3 {{ color: {t['primary']} !important; text-shadow: 0 0 10px {t['border']}; }}
        </style>
    """, unsafe_allow_html=True)

# ============================================================
# CHAVES / SECRETS
# ============================================================
_log = logging.getLogger("zeus.cofre")

def _limpar_chave(v: str) -> str:
    v = re.sub(r"\s+", "", v).strip("\"'")
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
    return _limpar_chave(os.environ.get(key) or default or "")

def _get_secret(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, "")

# ============================================================
# BANCO LOCAL
# ============================================================
DB_FILE = "zeus_chat.db"

def _conectar():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sessao ON messages(session_id, id)")
    conn.commit()
    return conn

def contar_mensagens() -> int:
    with contextlib.closing(_conectar()) as conn:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

def add_message(session_id, role, content):
    try:
        with contextlib.closing(_conectar()) as conn:
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
            conn.commit()
    except Exception as e:
        _log.warning("add_message: %s", e)

def get_recent_context(session_id, limit=6):
    try:
        with contextlib.closing(_conectar()) as conn:
            rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        return []

# ============================================================
# AUTO-APRENDIZADO LOCAL (SQLite + SequenceMatcher)
# ============================================================
DB_APRENDIZADO = "zeus_aprendizado.db"
_LOCK_APR = threading.RLock()

def _conectar_apr():
    conn = sqlite3.connect(DB_APRENDIZADO, timeout=30, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS pares (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta TEXT NOT NULL, resposta TEXT NOT NULL,
        acertos INTEGER DEFAULT 0, criado_em DATETIME DEFAULT CURRENT_TIMESTAMP)""")
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

# >>> FIM DA PARTE 1 <<<
# >>> INÍCIO DA PARTE 2 <<<
# ============================================================
# COFRE BACKBLAZE B2 (com debounce de 15s)
# ============================================================
COFRE_DB_KEY = "banco/zeus_chat.db"
COFRE_INTERVALO_SEG = 15.0
COFRE_BACKOFF_SEG = 30

@st.cache_resource
def _cofre_ctx():
    return {"lock": threading.RLock(), "nuvem_ok": False, "inicializado": False,
            "pendente": False, "ultimo_envio": 0.0, "bloqueado_ate": 0.0,
            "erro": "", "ultimo_tamanho": 0, "resultado_download": "", "modo_ativo": "s3"}

def _obter_config_cofre():
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    return endpoint, _secret("B2_BUCKET", "zeus-cofre-danilo"), _secret("B2_KEY_ID"), _secret("B2_APP_KEY")

@st.cache_resource
def _criar_cliente_s3(endpoint: str, key_id: str, app_key: str):
    import boto3
    from botocore.config import Config
    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id,
        aws_secret_access_key=app_key, region_name=m.group(1) if m else None,
        config=Config(signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=10, read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"}))

def _criar_cliente_b2_nativo(key_id: str, app_key: str):
    from b2sdk.v2 import B2Api, InMemoryAccountInfo
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", key_id, app_key)
    return api

def _obter_cliente_cofre():
    endpoint, bucket, key_id, app_key = _obter_config_cofre()
    if key_id and app_key:
        return _criar_cliente_s3(endpoint, key_id, app_key), bucket
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
    base = f"{type(e)._name_}: {e}"
    return f"{base} [{err.get('Code','')} - {err.get('Message','')}]" if (err.get("Code") or err.get("Message")) else base

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
                conn.execute("""INSERT INTO main.messages (session_id, role, content, timestamp)
                    SELECT n.session_id, n.role, n.content, n.timestamp FROM nuvem.messages n
                    WHERE NOT EXISTS (SELECT 1 FROM main.messages m
                        WHERE m.session_id = n.session_id AND m.role = n.role
                          AND m.content = n.content AND m.timestamp IS n.timestamp) ORDER BY n.id""")
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
    try:
        corpo = _baixar_corpo_s3(cliente, bucket)
        ctx["modo_ativo"] = "s3"
        return _processar_bytes_nuvem(corpo)
    except Exception as e_s3:
        if _eh_nao_encontrado(e_s3):
            ctx["nuvem_ok"], ctx["resultado_download"], ctx["modo_ativo"] = True, "ausente", "s3"
            return "ausente"
        raise RuntimeError(_detalhe_erro(e_s3)) from e_s3

def _enviar_para_nuvem(cliente, bucket, corpo):
    _enviar_corpo_s3(cliente, bucket, corpo)

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
            cliente, bucket = _obter_cliente_cofre()
            if not cliente or not bucket:
                ctx["erro"] = "Credenciais do B2 não encontradas (B2_KEY_ID / B2_APP_KEY)."
                ctx["bloqueado_ate"] = agora + COFRE_BACKOFF_SEG
                return False
            _baixar_e_mesclar(cliente, bucket)
            corpo = _snapshot_local()
            _enviar_para_nuvem(cliente, bucket, corpo)
            ctx.update(ultimo_envio=time.time(), pendente=False, erro="", ultimo_tamanho=len(corpo), bloqueado_ate=0.0)
            return True
        except Exception as e:
            ctx["erro"] = _detalhe_erro(e)
            ctx["bloqueado_ate"] = time.time() + COFRE_BACKOFF_SEG
            ctx["pendente"] = True
            _log.warning("Cofre B2 (envio): %s", ctx["erro"])
            return False

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
    if not bucket:
        return False, "Bucket do B2 não configurado."
    try:
        cliente.put_object(Bucket=bucket, Key=nome_limpo, Body=dados, ContentType="text/plain; charset=utf-8")
        return True, f"Arquivo salvo no B2 ({nome_limpo}, {len(dados)} bytes)."
    except Exception as e:
        return False, f"Falha: {_detalhe_erro(e)}"

def apagar_arquivo_cofre(nome_arquivo: str):
    nome_limpo = (nome_arquivo or "").strip().lstrip("/")
    if not nome_limpo:
        return False, "Informe o nome do arquivo."
    cliente, bucket = _obter_cliente_cofre()
    if not bucket:
        return False, "Bucket do B2 não configurado."
    try:
        cliente.delete_object(Bucket=bucket, Key=nome_limpo)
        return True, f"Arquivo apagado ({nome_limpo})."
    except Exception as e:
        return False, f"Falha: {_detalhe_erro(e)}"

def listar_arquivos_cofre(limite: int = 20):
    cliente, bucket = _obter_cliente_cofre()
    if not bucket:
        return False, "Bucket do B2 não configurado."
    try:
        resp = cliente.list_objects_v2(Bucket=bucket, MaxKeys=limite)
        itens = [obj["Key"] for obj in resp.get("Contents", [])]
        return True, ("Bucket vazio." if not itens else "Arquivos no B2:\n- " + "\n- ".join(itens))
    except Exception as e:
        return False, f"Falha: {_detalhe_erro(e)}"

def tratar_comando_cofre_chat(mensagem: str):
    txt = (mensagem or "").strip()
    baixo = txt.lower()
    if baixo.startswith("b2_listar"):
        return listar_arquivos_cofre()[1]
    if baixo.startswith("b2_salvar:"):
        corpo = txt[len("b2_salvar:"):].strip()
        nome, conteudo = corpo.split("|", 1) if "|" in corpo else ("cofre/teste_chat.txt", corpo)
        return salvar_arquivo_cofre(nome.strip(), conteudo.strip())[1]
    if baixo.startswith("b2_apagar:"):
        return apagar_arquivo_cofre(txt[len("b2_apagar:"):].strip())[1]
    return None

def mostrar_diagnostico_cofre():
    with st.expander("🔧 Diagnóstico do Cofre B2", expanded=False):
        if st.button("Testar envio agora", key="cofre_diag_btn", use_container_width=True):
            ok = sincronizar_db_cofre(forcar=True)
            (st.success if ok else st.error)(f"Envio concluído ({_cofre_ctx()['ultimo_tamanho']} bytes)." if ok else "Envio falhou.")
        else:
            st.caption("Toque no botão para testar a conexão.")

# ============================================================
# CLIENTE LLM PADRONIZADO (todos retornam True/False, str)
# ============================================================
SYS_PROMPT = (
    "Você é o Zeus, uma inteligência artificial soberana e autônoma criada por Danilo. "
    "Você NÃO é Claude, Gemini, GPT, Llama nem qualquer outro modelo: você é o Zeus. "
    "Sempre fale em primeira pessoa como Zeus. Tom cyberpunk, direto e técnico quando preciso. "
    "Responda em português. Seja objetivo."
)
PROVIDERS = ["groq", "gemini", "claude", "huggingface", "github", "deepinfra", "openrouter", "mistral", "cloudflare"]
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY",
        "huggingface": "HUGGINGFACE_API_KEY", "github": "GITHUB_API_KEY", "deepinfra": "DEEPINFRA_API_KEY",
        "openrouter": "OPENROUTER_API_KEY", "mistral": "MISTRAL_API_KEY", "cloudflare": "CLOUDFLARE_API_KEY"}
MODEL_SECRET = {"groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL", "claude": "ANTHROPIC_MODEL",
    "huggingface": "HF_MODEL", "github": "GITHUB_MODEL", "deepinfra": "DEEPINFRA_MODEL",
    "openrouter": "OPENROUTER_MODEL", "mistral": "MISTRAL_MODEL", "cloudflare": "CLOUDFLARE_MODEL"}
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
        try:
            ok, text = fn(model)
        except Exception as e:
            ok, text = False, f"exceção: {type(e)._name_}: {e}"
        if ok:
            return True, text
        last = str(text)[:200]
        if "credit balance" in last.lower():
            break
    return False, last

def _call_openai_compat(url, api_key, model, msgs, system=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    r = requests.post(url, headers=headers,
        json={"model": model, "messages": [{"role": "system", "content": system or SYS_PROMPT}] + msgs,
              "temperature": 0.4, "max_tokens": 700}, timeout=60)
    if r.status_code == 200:
        try:
            d = r.json()
            return True, (d["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            return True, (r.text or "").strip()
    return False, r.text[:200]

def _call_groq(api_key, model, msgs, system=None):
    return _call_openai_compat("https://api.groq.com/openai/v1/chat/completions", api_key, model, msgs, system)

def _call_claude(api_key, model, msgs, system=None):
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": system or SYS_PROMPT, "messages": msgs}, timeout=60)
    if r.status_code == 200:
        return True, "".join(b.get("text", "") for b in r.json()["content"])
    return False, r.text[:200]

def _call_gemini(api_key, model, msgs, system=None, ver="v1"):
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in msgs]
    r = requests.post(f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"systemInstruction": {"parts": [{"text": system or SYS_PROMPT}]}, "contents": contents}, timeout=60)
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

def _call_huggingface(api_key, model, msgs, system=None):
    chave = (api_key or "").strip() or _get_secret("HF_TOKEN") or _get_secret("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN", "")
    if not chave:
        return False, "chave HF não encontrada"
    modelo = (model or "").strip() or "Qwen/Qwen2.5-7B-Instruct"
    return _call_openai_compat("https://router.huggingface.co/v1/chat/completions", chave, modelo, msgs, system)

def _call_github(api_key, model, msgs, system=None):
    return _call_openai_compat("https://models.github.ai/inference/chat/completions",
        api_key, model, msgs, system, {"Accept": "application/json"})

def _call_mistral(api_key, model, msgs, system=None):
    key = (api_key or _secret("MISTRAL_API_KEY") or "").strip()
    if not key:
        return False, "MISTRAL_API_KEY ausente"
    payload = ([{"role": "system", "content": system}] if system else []) + msgs
    r = requests.post("https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model or "mistral-small-latest", "messages": payload}, timeout=30)
    if r.status_code != 200:
        return False, r.text[:200]
    d = r.json()
    txt = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    return (True, txt) if txt else (False, "vazio")

def _call_cloudflare(api_key, model, msgs, system=None):
    key = api_key or _get_secret("CLOUDFLARE_API_KEY")
    acc = _get_secret("CLOUDFLARE_ACCOUNT_ID")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/v1/chat/completions"
    return _call_openai_compat(url, key, model, msgs, system)

def _call_deepinfra(api_key, model, msgs, system=None):
    base = (_secret("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
    return _call_openai_compat(base + "/chat/completions", api_key, model, msgs, system)

def _call_openrouter(api_key, model, msgs, system=None):
    key = (api_key or _get_secret("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return False, "OPENROUTER_API_KEY ausente"
    payload = ([{"role": "system", "content": system}] if system else []) + msgs
    r = requests.post("https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://streamlit.io", "X-Title": "Zeus 3.0"},
        json={"model": model or "openrouter/free", "messages": payload}, timeout=30)
    if r.status_code != 200:
        return False, r.text[:200]
    d = r.json()
    txt = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    return (True, txt) if txt else (False, "vazio")

CALLERS = {"groq": _call_groq, "gemini": _call_gemini_auto, "claude": _call_claude,
    "huggingface": _call_huggingface, "github": _call_github, "deepinfra": _call_deepinfra,
    "openrouter": _call_openrouter, "mistral": _call_mistral, "cloudflare": _call_cloudflare}

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

# >>> FIM DA PARTE 2 <<<
# >>> INÍCIO DA PARTE 3 <<<
# ============================================================
# TELEGRAM (envio + leitura/resposta)
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
        return "❌ ERRO: Token do Telegram ou Chat ID não encontrados."
    msg = (texto or "").strip() or "Teste de telemetria."
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": destino, "text": f"🚨 [ZEUS] 🚨\n\n{msg[:3800]}", "parse_mode": "Markdown"}, timeout=30)
        if r.status_code == 200 and r.json().get("ok"):
            return "✅ SUCESSO: Mensagem enviada ao Telegram."
        return f"❌ FALHA TELEGRAM ({r.status_code}): {r.text[:200]}"
    except Exception as e:
        return f"❌ EXCEÇÃO TELEGRAM: {str(e)}"

def _rt_comando_telegram(texto: str) -> str:
    t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|enviar|envia|toque|mande)?\s*(no\s+|pelo\s+|via\s+)?(telegram|tg)\s*[:\-]?\s*", "", texto).strip(" .,!?:-")
    return zf_enviar_telegram(t or "Alerta disparado pelo criador Danilo.")

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
        dados = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=15).json()
    except Exception as e:
        st.sidebar.error(f"Erro Telegram: {e}")
        return 0
    if not dados.get("ok"):
        return 0
    respondidas = 0
    for upd in dados.get("result", []):
        uid = int(upd.get("update_id", 0))
        if uid > st.session_state.tg_last_update_id:
            st.session_state.tg_last_update_id = uid
        msg = upd.get("message") or upd.get("edited_message") or {}
        texto = (msg.get("text") or "").strip()
        cid = str((msg.get("chat") or {}).get("id") or "").strip()
        if not texto or not cid:
            continue
        if chat_id_autorizado and cid != str(chat_id_autorizado).strip():
            continue
        resposta = "⚡ Zeus ativo no Telegram." if texto == "/start" else (str(responder_fn(texto) or "Sem resposta.") if callable(responder_fn) else f"Recebido: {texto}")
        if "❌" not in zf_enviar_telegram(resposta, chat_id_destino=cid):
            respondidas += 1
    return respondidas

# ============================================================
# BUSCA NA WEB
# ============================================================
WEB_TRIGGERS = ("hoje", "agora", "atual", "atualmente", "últimas", "ultimas", "notícia", "noticia", "recente",
    "pesquise", "pesquisa", "busque", "procure", "google", "internet", "na web", "cotação", "cotacao",
    "dólar", "dolar", "preço", "preco", "clima", "previsão", "previsao", "resultado", "placar",
    "quem ganhou", "quem é o atual", "lançamento", "lancamento", "versão mais nova", "ao vivo",
    "selic", "ipca", "cdi", "bitcoin", "btc", "eth", "ethereum", "solana", "cripto", "bolsa", "ibovespa")

_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+", flags=re.UNICODE)
_STOP_PALAVRAS = {"por favor", "pfv", "pf", "zeus", "ei", "olá", "ola", "oi", "bom dia", "boa tarde",
    "boa noite", "me", "mim", "vc", "voce", "você", "para", "pra", "que", "qual", "quais", "sobre", "aqui",
    "agora", "por gentileza", "obrigado", "obrigada", "grato", "faco", "faço", "fazer", "saber", "dizer",
    "falar", "trazer", "traga", "mostre", "mande", "envie", "fonte", "fontes", "link", "links"}

def _limpar_query(texto, max_len=160):
    if not texto:
        return ""
    q = _RE_EMOJI.sub(" ", _RE_URL.sub(" ", str(texto)))
    q = re.sub(r"\s+", " ", q).strip().lower()
    tokens = [t for t in q.split() if t.strip(".,;:!?") not in _STOP_PALAVRAS]
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

def buscar_web_tavily(query: str, max_results: int = 5) -> str:
    api_key = (st.secrets.get("TAVILY_API_KEY", "") if hasattr(st, "secrets") else "") or os.getenv("TAVILY_API_KEY", "")
    api_key = api_key.strip()
    if not api_key:
        return ""
    try:
        resp = requests.post("https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "search_depth": "basic", "include_answer": True, "max_results": int(max_results or 5)}, timeout=15)
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
        r = requests.get("https://pt.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": int(n or 4)},
            headers={"User-Agent": "ZeusBot/4.0"}, timeout=10)
        if r.status_code != 200:
            return ""
        itens = r.json().get("query", {}).get("search", [])
        return "\n".join(
            f"- {i.get('title','').strip()}: {re.sub(r'<[^>]+>', '', i.get('snippet','')).strip()} "
            f"(https://pt.wikipedia.org/wiki/{i.get('title','').replace(' ', '_')})"
            for i in itens if i.get("title"))
    except Exception:
        return ""

def web_search(q, n=5):
    q_limpa = _limpar_query(q) or (q or "").strip()
    if not q_limpa:
        return "", ""
    r = buscar_web_tavily(q_limpa, max_results=n)
    if r and r.strip():
        return r.strip(), "Tavily"
    for nome, fn in (("Bing/DuckDuckGo/Yahoo", _search_multi), ("Wikipédia", _search_wiki)):
        try:
            txt = (fn(q_limpa, n) or "").strip()
        except Exception:
            txt = ""
        if txt and ("Wikipédia" not in nome or _resultado_no_tema(q_limpa, txt)):
            return txt, nome
    return "", ""

def precisa_web(prompt):
    modo = st.session_state.get("web_mode", "Automática")
    if modo == "Desligada":
        return False
    if modo == "Sempre":
        return True
    low = (prompt or "").lower()
    return any(t in low for t in WEB_TRIGGERS) or bool(re.search(r"\b20(2[4-9]|3[0-9])\b", low)) or len(low.split()) <= 5

def enriquecer_com_web(prompt):
    hoje = time.strftime("%d/%m/%Y")
    if not precisa_web(prompt):
        return prompt, "llm_direto"
    ctx_web, fonte = web_search(prompt)
    if not ctx_web:
        return (f"[CONTEXTO WEB em {hoje}: sem resultados]\nResponda com seu conhecimento.\nPergunta: {prompt}"), "fallback_llm"
    return (f"[DADOS DA INTERNET em {hoje}, via {fonte}]\n{ctx_web[:4500]}\n[FIM]\nPergunta: {prompt}"), fonte

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

def _img_pollinations(prompt):
    from urllib.parse import quote
    ultimo = ""
    for tentativa in range(2):
        r = requests.get("https://image.pollinations.ai/prompt/" + quote(prompt[:600]),
            params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
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
    r = requests.post("https://api.deepinfra.com/v1/openai/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"prompt": prompt, "size": "1024x1024", "n": 1, "model": modelo}, timeout=120)
    if r.status_code == 200:
        return base64.b64decode(r.json()["data"][0]["b64_json"])
    raise RuntimeError(f"({r.status_code}) {r.text[:60]}")

def _img_gemini(prompt):
    key = _secret("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("sem chave")
    model = _secret("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:100]}")
    for part in r.json()["candidates"][0]["content"]["parts"]:
        d = part.get("inlineData") or part.get("inline_data")
        if d:
            return base64.b64decode(d["data"])
    raise RuntimeError("Gemini não devolveu imagem")

IMAGE_CASCADE = [("DeepInfra", _img_deepinfra), ("Gemini", _img_gemini), ("Pollinations", _img_pollinations)]

def make_image(prompt, log):
    for nome, fn in IMAGE_CASCADE:
        try:
            data = fn(prompt)
            log.append("✅ Imagem criada por: " + nome)
            return data
        except Exception as e:
            log.append("❌ Imagem " + nome + ": " + str(e)[:160])
    return None

def make_video(prompt, log, img=None):
    log.append("⚪ Vídeo experimental: use HUGGINGFACE_API_KEY + HF_VIDEO_MODEL para ativar")
    return None

# ============================================================
# VISÃO
# ============================================================
def _vis_openai(url, key, model, img_b64, mime, prompt, extra=None):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    r = requests.post(url, headers=headers, json={"model": model, "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + img_b64}}]}]}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"({r.status_code}) {r.text[:90]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()

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
    key = _secret("GEMINI_API_KEY")
    if not key:
        return "❌ GEMINI_API_KEY ausente."
    try:
        return _gemini_generate(key, {"contents": [{"role": "user", "parts": [
            {"text": pergunta}, {"inline_data": {"mime_type": mime, "data": img_b64}}]}]})
    except Exception as e:
        return "❌ Erro: " + str(e)[:200]

def _vis_gemini(k, b, m, p):
    return _gemini_generate(k, {"contents": [{"role": "user", "parts": [
        {"text": p}, {"inline_data": {"mime_type": m, "data": b}}]}]})

def _vis_groq(k, b, m, p):
    return _vis_openai("https://api.groq.com/openai/v1/chat/completions", k,
        "meta-llama/llama-4-scout-17b-16e-instruct", b, m, p)

VISION_CASCADE = {"gemini": _vis_gemini, "groq": _vis_groq}

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
            continue
        try:
            txt = VISION_CASCADE[prov](key, img_b64, mime, comando)
            if txt:
                log.append("✅ Visão: " + prov)
                return txt, prov, log
        except Exception as e:
            log.append("❌ Visão " + prov + ": " + str(e)[:120])
    return None, None, log

# ============================================================
# PIPELINE
# ============================================================
def _llm(prompt, system, log, etapa):
    ok, texto, prov, erros = get_llm_response(st.session_state.session_id, prompt,
        st.session_state.get("model_sel", "groq"), system=system, use_history=False)
    if ok:
        log.append("✅ " + etapa + ": " + str(prov))
        return texto
    log.append("❌ " + etapa + ": " + texto[:150])
    return ""

def run_pipeline(pedido, quer_img, quer_vid):
    log = []
    contexto, fonte = web_search(pedido)
    log.append("🔎 Busca (" + fonte + "): ok" if contexto else "⚪ Busca: sem resultados")
    resumo = _llm("Pedido: " + pedido + "\n\nContexto:\n" + contexto[:4000],
        "Resuma em português, em até 8 linhas, os fatos úteis.", log, "Resumo") if contexto else ""
    bruto = _llm("Pedido: " + pedido + "\n\nFatos:\n" + resumo,
        "Responda EXATAMENTE 2 linhas:\nIMAGEM: <prompt em inglês>\nVIDEO: <prompt em inglês>", log, "Prompt")
    m_img = re.search(r"IMAGEM:\s*(.+)", bruto or "")
    pimg = m_img.group(1).strip() if m_img else pedido
    img = make_image(pimg, log) if quer_img else None
    return {"log": log, "resumo": resumo, "prompt_img": pimg, "img": img}

def render_pipeline_ui():
    with st.expander("🔗 Pipeline Multi-IA", expanded=False):
        pedido = st.text_area("O que criar?", key="pipe_req")
        if st.button("🚀 Executar", key="pipe_run") and pedido.strip():
            with st.spinner("Encadeando IAs..."):
                st.session_state.pipe_res = run_pipeline(pedido.strip(), True, False)
        res = st.session_state.get("pipe_res")
        if res:
            if res["resumo"]:
                st.write(res["resumo"])
            if res["img"]:
                st.image(res["img"], caption="Imagem gerada")
            with st.expander("Log", expanded=not res["img"]):
                st.write("\n\n".join(res["log"]))

# >>> FIM DA PARTE 3 <<<
# >>> INÍCIO DA PARTE 4 <<<
# ============================================================
# 4 AVATARES THREE.JS (Olho, Esfera, Robô, Zeus)
# ============================================================
_AVATAR_BASE = r"""
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;background:#0b0f19;overflow:hidden;font-family:monospace}
#c{width:100%;height:100%;display:block}
.fb{color:#00ffcc;text-align:center;padding-top:35%;font-size:13px;display:none}</style>
</head><body><canvas id="c"></canvas><div class="fb" id="fb">⚡ Avatar carregando...</div>
<script src="https://unpkg.com/three@0.160.0/build/three.min.js"></script>
<script>
(function(){
  try{
    if(typeof THREE==='undefined') throw new Error('THREE não carregou');
    const canvas=document.getElementById('c');
    const renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
    const scene=new THREE.Scene();
    const camera=new THREE.PerspectiveCamera(45,1,0.1,100);
    camera.position.set(0,0,4.5);
    scene.add(new THREE.AmbientLight(0x223344,1.4));
    const key=new THREE.DirectionalLight(0x00ffcc,1.5); key.position.set(2,3,4); scene.add(key);
    const rim=new THREE.DirectionalLight(0x0077ff,1.1); rim.position.set(-3,-1,2); scene.add(rim);
    const MODE = "MODE";
    let obj;
    if(MODE==='eye'){
      const g=new THREE.SphereGeometry(1.0,64,64);
      const m=new THREE.MeshStandardMaterial({color:0xe8f0ff,roughness:0.3,metalness:0.2});
      obj=new THREE.Mesh(g,m); scene.add(obj);
      const iris=new THREE.Mesh(new THREE.CircleGeometry(0.45,64),
        new THREE.MeshBasicMaterial({color:0x00ffcc})); iris.position.z=1.001; obj.add(iris);
      const pup=new THREE.Mesh(new THREE.CircleGeometry(0.18,64),
        new THREE.MeshBasicMaterial({color:0x000000})); pup.position.z=1.002; obj.add(pup);
    } else if(MODE==='sphere'){
      const g=new THREE.SphereGeometry(1.2,64,64);
      const m=new THREE.MeshStandardMaterial({color:0x00ffcc,wireframe:true,emissive:0x00ffcc,emissiveIntensity:0.6});
      obj=new THREE.Mesh(g,m); scene.add(obj);
    } else if(MODE==='robot'){
      const grp=new THREE.Group();
      const mat=new THREE.MeshStandardMaterial({color:0xc8d4e0,metalness:0.7,roughness:0.3});
      const head=new THREE.Mesh(new THREE.BoxGeometry(1.4,1.2,1.2),mat); grp.add(head);
      const eyeMat=new THREE.MeshBasicMaterial({color:0x00ffcc});
      const eL=new THREE.Mesh(new THREE.SphereGeometry(0.13,20,20),eyeMat); eL.position.set(-0.32,0.15,0.62); grp.add(eL);
      const eR=eL.clone(); eR.position.x=0.32; grp.add(eR);
      const ant=new THREE.Mesh(new THREE.CylinderGeometry(0.03,0.03,0.5),mat); ant.position.y=0.85; grp.add(ant);
      const tip=new THREE.Mesh(new THREE.SphereGeometry(0.09,20,20),eyeMat); tip.position.y=1.15; grp.add(tip);
      obj=grp; scene.add(obj);
    } else {
      const g=new THREE.SphereGeometry(1.15,64,64);
      const m=new THREE.MeshStandardMaterial({color:0xd8dde3,roughness:0.55,metalness:0.08});
      obj=new THREE.Mesh(g,m); scene.add(obj);
      const eyeMat=new THREE.MeshBasicMaterial({color:0xffffff});
      const eL=new THREE.Mesh(new THREE.SphereGeometry(0.085,20,20),eyeMat); eL.position.set(-0.32,0.15,1.02); obj.add(eL);
      const eR=eL.clone(); eR.position.x=0.32; obj.add(eR);
      const gl=new THREE.PointLight(0x00ffcc,1.2,1.2); gl.position.copy(eL.position); obj.add(gl);
      const gr=gl.clone(); gr.position.copy(eR.position); obj.add(gr);
    }
    const N=160,pos=new Float32Array(N*3);
    for(let i=0;i<N;i++){pos[i*3]=(Math.random()-.5)*6;pos[i*3+1]=(Math.random()-.5)*6;pos[i*3+2]=(Math.random()-.5)*4;}
    const pg=new THREE.BufferGeometry(); pg.setAttribute('position',new THREE.BufferAttribute(pos,3));
    const poeira=new THREE.Points(pg,new THREE.PointsMaterial({color:0x00ffcc,size:0.025,transparent:true,opacity:0.6}));
    scene.add(poeira);
    function resize(){const w=canvas.clientWidth||canvas.parentElement.clientWidth||300;
      const h=canvas.clientHeight||canvas.parentElement.clientHeight||220;
      renderer.setSize(w,h,false); camera.aspect=w/h; camera.updateProjectionMatrix();}
    window.addEventListener('resize',resize); resize();
    let t=0;
    (function loop(){t+=0.01;
      obj.rotation.y=Math.sin(t*0.6)*0.4;
      obj.rotation.x=Math.sin(t*0.4)*0.08;
      poeira.rotation.y=t*0.05;
      renderer.render(scene,camera); requestAnimationFrame(loop);})();
  }catch(e){
    document.getElementById('c').style.display='none';
    const fb=document.getElementById('fb'); fb.style.display='block';
    fb.textContent='⚡ Avatar 3D indisponível ('+e.message+')';
  }
})();
</script></body></html>
"""

def render_avatar(modo: str = "zeus", altura: int = 240):
    html = AVATAR_BASE.replace("_MODE", modo).replace('"MODE"', f'"{modo}"')
    components.html(html, height=altura, scrolling=False)

def render_avatares_sidebar():
    st.sidebar.markdown("### ⚡ Avatar")
    modo = st.sidebar.radio(
        "Escolha",
        ["👁️ Olho", "🌐 Esfera 3D", "🤖 Robô", "⚡ Zeus"],
        key="avatar_modo",
        horizontal=False,
        label_visibility="collapsed",
    )
    mapa = {"👁️ Olho": "eye", "🌐 Esfera 3D": "sphere", "🤖 Robô": "robot", "⚡ Zeus": "zeus"}
    render_avatar(mapa.get(modo, "zeus"), 220)

# ============================================================
# SPOTIFY LATERAL
# ============================================================
API_SP = "https://api.spotify.com/v1"
TOKEN_URL_SP = "https://accounts.spotify.com/api/token"
_cache_tok = {"tok": None, "exp": 0}

def _sp_secret(k, d=""):
    try:
        return st.secrets.get(k, d) or os.getenv(k, d)
    except Exception:
        return os.getenv(k, d)

def sp_get_token():
    cid = _sp_secret("SPOTIFY_CLIENT_ID")
    cs = _sp_secret("SPOTIFY_CLIENT_SECRET")
    rt = _sp_secret("SPOTIFY_REFRESH_TOKEN")
    if not (cid and cs and rt):
        return None
    if _cache_tok["tok"] and time.time() < _cache_tok["exp"] - 30:
        return _cache_tok["tok"]
    try:
        r = requests.post(
            TOKEN_URL_SP,
            data={"grant_type": "refresh_token", "refresh_token": rt},
            auth=(cid, cs),
            timeout=20,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        _cache_tok["tok"] = d["access_token"]
        _cache_tok["exp"] = time.time() + int(d.get("expires_in", 3600))
        return d["access_token"]
    except Exception:
        return None

def sp_h():
    t = sp_get_token()
    return {"Authorization": f"Bearer {t}"} if t else None

def sp_status():
    h = sp_h()
    if not h:
        return {"ok": False, "msg": "Spotify não configurado."}
    try:
        r = requests.get(f"{API_SP}/me/player", headers=h, timeout=10)
        if r.status_code == 204:
            return {
                "ok": True,
                "tocando": False,
                "nome": "—",
                "artista": "—",
                "capa": "",
                "progresso_ms": 0,
                "duracao_ms": 0,
                "volume": 50,
            }
        if r.status_code != 200:
            return {"ok": False, "msg": f"HTTP {r.status_code}"}
        d = r.json()
        item = d.get("item") or {}
        return {
            "ok": True,
            "tocando": bool(d.get("is_playing")),
            "nome": item.get("name", "—"),
            "artista": ", ".join(a["name"] for a in item.get("artists", [])) or "—",
            "capa": (item.get("album", {}).get("images") or [{}])[0].get("url", ""),
            "progresso_ms": d.get("progress_ms", 0),
            "duracao_ms": item.get("duration_ms", 0),
            "volume": (d.get("device") or {}).get("volume_percent", 50),
        }
    except Exception as e:
        return {"ok": False, "msg": str(e)[:120]}

def sp_acao(cmd, **params):
    h = sp_h()
    if not h:
        return False, "Sem token Spotify"
    try:
        if cmd == "play":
            r = requests.put(f"{API_SP}/me/player/play", headers=h, json=params or None, timeout=10)
        elif cmd == "pause":
            r = requests.put(f"{API_SP}/me/player/pause", headers=h, timeout=10)
        elif cmd == "next":
            r = requests.post(f"{API_SP}/me/player/next", headers=h, timeout=10)
        elif cmd == "prev":
            r = requests.post(f"{API_SP}/me/player/previous", headers=h, timeout=10)
        elif cmd == "volume":
            r = requests.put(
                f"{API_SP}/me/player/volume",
                headers=h,
                params={"volume_percent": params["pct"]},
                timeout=10,
            )
        elif cmd == "tocar":
            q = params["q"]
            r1 = requests.get(
                f"{API_SP}/search",
                headers=h,
                params={"q": q, "type": "track", "limit": 1},
                timeout=15,
            )
            tr = r1.json().get("tracks", {}).get("items", []) if r1.status_code == 200 else []
            if not tr:
                return False, "Nada encontrado"
            requests.put(
                f"{API_SP}/me/player/play",
                headers=h,
                json={"uris": [tr[0]["uri"]]},
                timeout=10,
            )
            return True, f"Tocando: {tr[0]['name']}"
        elif cmd == "artista":
            nome = params["nome"]
            r1 = requests.get(
                f"{API_SP}/search",
                headers=h,
                params={"q": nome, "type": "artist", "limit": 1},
                timeout=15,
            )
            arts = r1.json().get("artists", {}).get("items", []) if r1.status_code == 200 else []
            if not arts:
                return False, f"Artista não encontrado: {nome}"
            aid = arts[0]["id"]
            r2 = requests.get(
                f"{API_SP}/artists/{aid}/top-tracks",
                headers=h,
                params={"market": "BR"},
                timeout=15,
            )
            uris = [t["uri"] for t in r2.json().get("tracks", [])[:20]] if r2.status_code == 200 else []
            if not uris:
                return False, "Sem faixas"
            requests.put(f"{API_SP}/me/player/play", headers=h, json={"uris": uris}, timeout=10)
            return True, f"Tocando {len(uris)} faixas de {nome}"
        else:
            return False, "Comando desconhecido"
        return r.status_code in (200, 204), f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:120]

def render_spotify_sidebar():
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🎧 Spotify")
    if not sp_get_token():
        st.sidebar.warning("Spotify não configurado (secrets).")
        st.sidebar.caption("Adicione SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN em .streamlit/secrets.toml")
        return
    s = sp_status()
    if not s.get("ok"):
        st.sidebar.error(s.get("msg", "Erro Spotify"))
        return
    if s.get("capa"):
        st.sidebar.image(s["capa"], width=170)
    st.sidebar.caption(f"{s.get('nome','—')}\n\n{s.get('artista','—')}")
    if s.get("duracao_ms"):
        st.sidebar.progress(min(max(s["progresso_ms"] / s["duracao_ms"], 0.0), 1.0))
    c1, c2, c3 = st.sidebar.columns(3)
    if c1.button("⏮️", key="sp_prev"):
        sp_acao("prev")
    if c2.button("⏯️", key="sp_play"):
        sp_acao("pause" if s["tocando"] else "play")
    if c3.button("⏭️", key="sp_next"):
        sp_acao("next")
    vol = int(s.get("volume", 50))
    c4, c5, c6 = st.sidebar.columns(3)
    if c4.button("🔉", key="sp_vm"):
        sp_acao("volume", pct=max(0, vol - 1))
    c5.write(f"🔊 {vol}%")
    if c6.button("🔊+", key="sp_vp"):
        sp_acao("volume", pct=min(100, vol + 1))
    st.sidebar.markdown("Pedir música")
    pedido = st.sidebar.text_input("Ex.: tocar Legião Urbana", key="sp_pedido")
    a, b = st.sidebar.columns(2)
    if a.button("🎵 Tocar", key="sp_tocar") and pedido.strip():
        ok, msg = sp_acao("tocar", q=pedido.strip())
        (st.sidebar.success if ok else st.sidebar.error)(msg)
    if b.button("🎤 Artista", key="sp_artista") and pedido.strip():
        ok, msg = sp_acao("artista", nome=pedido.strip())
        (st.sidebar.success if ok else st.sidebar.error)(msg)

# ============================================================
# ============================================================
# MAIN
# ============================================================
def processar_chat(prompt: str) -> str:
    interceptado, resp_tg = interceptar_comando_autonomo_telegram(prompt)
    if interceptado:
        return resp_tg or ""

    prompt_enriquecido, fonte = enriquecer_com_web(prompt)
    ok, texto, prov, erros = get_llm_response(
        st.session_state.session_id,
        prompt_enriquecido,
        st.session_state.get("model_sel", "groq"),
    )
    if ok:
        st.session_state.last_info = f"✅ {prov}" + (f" · 🌐 {fonte}" if fonte else "")
        return texto or ""

    err_msg = (texto or ", ".join(erros or []) or "Erro desconhecido")[:200]
    st.session_state.last_info = "❌ " + err_msg
    return texto or err_msg


def main():
    inject_theme("Cyberpunk")
    if "session_id" not in st.session_state:
        st.session_state.session_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "model_sel" not in st.session_state:
        st.session_state.model_sel = "groq"
    if "web_mode" not in st.session_state:
        st.session_state.web_mode = "Automática"

    init_db()

    # Sidebar
    st.sidebar.title("⚡ Zeus 3.0")
    render_avatares_sidebar()
    st.sidebar.markdown("---")

    provedores_disp = PROVIDERS if PROVIDERS else ["groq"]
    idx_modelo = (
        provedores_disp.index(st.session_state.model_sel)
        if st.session_state.model_sel in provedores_disp
        else 0
    )
    st.session_state.model_sel = st.sidebar.selectbox(
        "Modelo",
        provedores_disp,
        index=idx_modelo,
        key="sel_modelo",
    )

    opcoes_web = ["Automática", "Sempre", "Desligada"]
    idx_web = (
        opcoes_web.index(st.session_state.web_mode)
        if st.session_state.web_mode in opcoes_web
        else 0
    )
    st.session_state.web_mode = st.sidebar.selectbox(
        "Busca web",
        opcoes_web,
        index=idx_web,
        key="sel_web",
    )

    st.sidebar.markdown("---")
    render_spotify_sidebar()
    st.sidebar.markdown("---")

    s = estatisticas_aprendizado() or {"total": 0, "top": []}
    st.sidebar.markdown("### 🧠 Aprendizado local")
    st.sidebar.metric("Pares memorizados", s.get("total", 0))
    for p, n in s.get("top", []):
        st.sidebar.caption(f"• {str(p)[:50]}… ({n}x)")

    if st.sidebar.button("🔄 Sincronizar B2", key="btn_sync_b2"):
        try:
            ok = sincronizar_db_cofre(forcar=True)
            (st.sidebar.success if ok else st.sidebar.error)(
                "Sincronizado" if ok else "Falhou"
            )
        except Exception as e:
            st.sidebar.error(f"Erro ao sincronizar B2: {e}")

    st.sidebar.markdown("---")
    if st.sidebar.button("📨 Ler Telegram", key="btn_tg"):
        try:
            n = sincronizar_e_responder_telegram(responder_fn=processar_chat)
            st.sidebar.info(f"{n} mensagem(ns) respondida(s)")
        except Exception as e:
            st.sidebar.error(f"Erro ao ler Telegram: {e}")

    mostrar_diagnostico_cofre()

    # Cabeçalho
    st.title("⚡ Zeus 3.0")
    st.caption("IA soberana · B2 · Auto-aprendizado · Telegram · Spotify · 4 avatares")

    # Chat
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Fale com o Zeus…")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        add_message(st.session_state.session_id, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Zeus processando…"):
                resposta = processar_chat(prompt)
            st.markdown(resposta)
        st.session_state.messages.append({"role": "assistant", "content": resposta})
        add_message(st.session_state.session_id, "assistant", resposta)
        st.rerun()

    if st.session_state.get("last_info"):
        st.caption("ℹ️ " + st.session_state.last_info)

    # Expanders
    render_pipeline_ui()

    # Ensino no chat
    with st.expander("🧠 Ensinar Zeus", expanded=False):
        per = st.text_area("Pergunta", key="ens_per")
        resp = st.text_area("Resposta esperada", key="ens_resp")
        if st.button("💾 Salvar", key="ens_save") and per.strip() and resp.strip():
            if aprender_resposta(per.strip(), resp.strip()):
                st.success("Ensinado!")
            else:
                st.error("Falhou.")


if __name__ == "__main__":
    main()

# >>> FIM DA PARTE 4 (fim do arquivo) <<<
