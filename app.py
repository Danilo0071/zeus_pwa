"""
ZEUS :: AÇÕES  (arquivo novo, fica na RAIZ do repositório, ao lado do app.py)

Por que existe: no Streamlit o script roda de cima para baixo a cada interação.
O chat chama processar_prompt() no MEIO do app.py, mas o roteador de comandos
(e-mail, GitHub Action, habilidades...) estava definido mais ABAIXO, então ainda
não existia quando o chat o chamava. Aqui tudo é definido ANTES de ser usado.

Liga no app.py com:   import zeus_acoes
                      processar_prompt = zeus_acoes.instalar(globals())
Secrets usados (todos opcionais; cada ação avisa o que falta):
  B2_ENDPOINT B2_BUCKET B2_KEY_ID B2_APP_KEY
  GITHUB_API_KEY (ou GITHUB_TOKEN) GITHUB_USER GITHUB_REPO
  GITHUB_BRANCH GITHUB_WORKFLOW GITHUB_WORKFLOW_TELEGRAM
  TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS EMAIL_FROM EMAIL_TO
  SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET SPOTIFY_REFRESH_TOKEN SPOTIFY_REDIRECT_URI
Nada roda sozinho: cada ação só acontece porque VOCÊ pediu no chat ou apertou o botão.
"""
import contextlib
import csv
import html as _html
import io
import json
import os
import re
import smtplib
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.message import EmailMessage

import streamlit as st

_G = {}  # globals do app.py (preenchido por instalar)
UA = {"User-Agent": "Mozilla/5.0 (Zeus/3.0)"}
BINANCE_URL = "https://data-api.binance.vision/api/v3"   # só dados públicos de mercado
PROTEGIDOS = {"banco/zeus_chat.db", "autonomo/diario.json", "memoria/fatos.json"}


# ============================================================
# BÁSICO: segredos, HTTP, formatação
# ============================================================
def _limpar(v: str) -> str:
    v = re.sub(r"\s+", "", str(v or "")).strip("\"'")
    return "" if v.lower().startswith(("cole", "sua_chave", "seu_", "your_")) else v


def _secret(chave: str, padrao: str = "") -> str:
    """Topo dos secrets, depois dentro de [seções], depois variáveis de ambiente."""
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
            return _limpar(val)
    except Exception:
        pass
    return _limpar(os.environ.get(chave) or padrao)


def _http(metodo, url, headers=None, corpo=None, timeout=25):
    """Retorna (status, texto). Nunca levanta exceção."""
    h = dict(UA)
    h.update(headers or {})
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    try:
        req = urllib.request.Request(url, data=dados, headers=h, method=metodo)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            txt = ""
        return e.code, txt
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _bytes_fmt(n) -> str:
    n = float(n or 0)
    for un in ("B", "KB", "MB", "GB"):
        if n < 1024 or un == "GB":
            return f"{n:.0f} {un}" if un == "B" else f"{n:.1f} {un}"
        n /= 1024


def _agora():
    return datetime.now(timezone.utc)


def _norm(txt: str) -> str:
    t = unicodedata.normalize("NFKD", str(txt or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", " ", t)


def _det(e: Exception) -> str:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error") or {}
    base = f"{type(e).__name__}: {e}"
    if err.get("Code"):
        base += f" [{err.get('Code')}]"
    return base[:300]


# ============================================================
# COFRE B2 (S3 primeiro, b2sdk de reserva)
# ============================================================
def _b2_cfg():
    endpoint = _secret("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    if endpoint and not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    return endpoint, _secret("B2_BUCKET"), _secret("B2_KEY_ID"), _secret("B2_APP_KEY")


@st.cache_resource
def _s3_cached(endpoint, key_id, app_key):
    import boto3
    from botocore.config import Config
    m = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
    base = dict(signature_version="s3v4", connect_timeout=10, read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"})
    try:   # boto3 novo manda checksums que o B2 recusa; isto desliga
        cfg = Config(request_checksum_calculation="when_required",
                     response_checksum_validation="when_required", **base)
    except TypeError:
        cfg = Config(**base)
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id,
                        aws_secret_access_key=app_key, region_name=m.group(1) if m else None, config=cfg)


@st.cache_resource
def _nativo_cached(key_id, app_key, bucket):
    from b2sdk.v2 import B2Api, InMemoryAccountInfo
    api = B2Api(InMemoryAccountInfo())
    api.authorize_account("production", key_id, app_key)
    return api.get_bucket_by_name(bucket)


def _nao_encontrado(e) -> bool:
    resp = getattr(e, "response", None) or {}
    cod = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if cod == "NoSuchBucket":
        return False
    return cod in ("NoSuchKey", "404", "NotFound") or status == 404 or type(e).__name__ == "FileNotPresent"


def _b2_rodar(fn_s3, fn_nativo):
    endpoint, bucket, key_id, app_key = _b2_cfg()
    if not (bucket and key_id and app_key):
        raise RuntimeError("Cofre B2 sem credenciais (B2_BUCKET, B2_KEY_ID, B2_APP_KEY nos Secrets).")
    try:
        return fn_s3(_s3_cached(endpoint, key_id, app_key), bucket)
    except Exception as e1:
        if _nao_encontrado(e1):
            raise FileNotFoundError("arquivo não encontrado no cofre") from e1
        try:
            return fn_nativo(_nativo_cached(key_id, app_key, bucket))
        except ImportError:
            raise RuntimeError(_det(e1)) from e1
        except Exception as e2:
            if _nao_encontrado(e2):
                raise FileNotFoundError("arquivo não encontrado no cofre") from e2
            raise RuntimeError(f"S3: {_det(e1)} | b2sdk: {_det(e2)}") from e2


def b2_put(chave: str, dados: bytes, ctype: str = "application/octet-stream"):
    _b2_rodar(
        lambda s3, b: s3.put_object(Bucket=b, Key=chave, Body=dados, ContentType=ctype),
        lambda bk: bk.upload_bytes(dados, chave, content_type=ctype),
    )


def b2_get(chave: str) -> bytes:
    def nativo(bk):
        buf = io.BytesIO()
        bk.download_file_by_name(chave).save(buf)
        return buf.getvalue()
    return _b2_rodar(lambda s3, b: s3.get_object(Bucket=b, Key=chave)["Body"].read(), nativo)


def b2_list(prefixo: str = "", limite: int = 40):
    """Lista de (chave, tamanho)."""
    def s3(c, b):
        r = c.list_objects_v2(Bucket=b, Prefix=prefixo, MaxKeys=limite)
        return [(o["Key"], o.get("Size", 0)) for o in r.get("Contents", [])]

    def nativo(bk):
        itens = []
        for fv, _ in bk.ls(folder_to_list=prefixo.rstrip("/"), latest_only=True, recursive=True):
            itens.append((fv.file_name, getattr(fv, "size", 0)))
            if len(itens) >= limite:
                break
        return itens
    return _b2_rodar(s3, nativo)


def b2_delete(chave: str):
    def s3(c, b):
        c.head_object(Bucket=b, Key=chave)          # falha com 404 se não existir
        c.delete_object(Bucket=b, Key=chave)

    def nativo(bk):
        fv = bk.get_file_info_by_name(chave)
        bk.delete_file_version(fv.id_, chave)
    _b2_rodar(s3, nativo)


# ============================================================
# DADOS DE MERCADO (Binance público + Banco Central) -> cofre
# ============================================================
BIN_ALIAS = [("bitcoin", "BTCUSDT"), ("btc", "BTCUSDT"), ("ethereum", "ETHUSDT"), ("eth", "ETHUSDT"),
             ("bnb", "BNBUSDT"), ("solana", "SOLUSDT"), ("sol", "SOLUSDT"), ("xrp", "XRPUSDT"),
             ("cardano", "ADAUSDT"), ("ada", "ADAUSDT"), ("dogecoin", "DOGEUSDT"), ("doge", "DOGEUSDT")]
BCB_ALIAS = [("selic diaria", 11), ("selic", 432), ("ipca", 433), ("inflacao", 433),
             ("dolar", 1), ("cdi", 12)]
BCB_NOMES = {432: "Meta Selic", 433: "IPCA mensal", 1: "Dólar (venda)", 12: "CDI diário", 11: "Selic diária"}
INTERVALOS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w")


def _ms_iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _svg_linha(titulo, valores, ini, fim):
    W, H, P = 800, 320, 44
    n = len(valores)
    mn, mx = min(valores), max(valores)
    span = (mx - mn) or 1.0
    pts = " ".join(f"{P + i * (W - 2 * P) / max(1, n - 1):.1f},{H - P - (v - mn) / span * (H - 2 * P):.1f}"
                   for i, v in enumerate(valores))
    t = _html.escape(titulo)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
        f'<rect width="{W}" height="{H}" fill="#0b0f19"/>'
        f'<text x="{P}" y="24" fill="#00ffcc" font-family="monospace" font-size="15">{t}</text>'
        f'<polyline fill="none" stroke="#00ffcc" stroke-width="2" points="{pts}"/>'
        f'<text x="{P}" y="{H - 12}" fill="#8aa" font-family="monospace" font-size="11">{_html.escape(ini)}</text>'
        f'<text x="{W - P}" y="{H - 12}" fill="#8aa" font-family="monospace" font-size="11" text-anchor="end">{_html.escape(fim)}</text>'
        f'<text x="{P}" y="{H - P - 6}" fill="#8aa" font-family="monospace" font-size="11">min {mn:g}</text>'
        f'<text x="{W - P}" y="{P + 4}" fill="#8aa" font-family="monospace" font-size="11" text-anchor="end">max {mx:g}</text>'
        f'</svg>')


def coletar_binance(simbolo: str, intervalo: str = "1h", limite: int = 500) -> str:
    q = urllib.parse.urlencode({"symbol": simbolo, "interval": intervalo, "limit": limite})
    st_, txt = _http("GET", f"{BINANCE_URL}/klines?{q}")
    if st_ == 451:
        return f"❌ Binance {simbolo}: acesso bloqueado nesta região (HTTP 451)."
    if st_ != 200:
        return f"❌ Binance {simbolo}: HTTP {st_} {txt[:120]}"
    velas = json.loads(txt)
    if not isinstance(velas, list) or not velas:
        return f"❌ Binance {simbolo}: resposta vazia (símbolo inválido?)."
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades"])
    for k in velas:
        w.writerow([_ms_iso(k[0]), k[1], k[2], k[3], k[4], k[5], _ms_iso(k[6]), k[7], k[8]])
    corpo = buf.getvalue().encode("utf-8")
    fechos = [float(k[4]) for k in velas]
    ini, fim = _ms_iso(velas[0][0]), _ms_iso(velas[-1][0])
    base = f"dados/binance/{simbolo}_{intervalo}"
    chaves = [f"{base}_{_agora():%Y%m%d_%H%M}.csv", f"{base}_ultimo.csv"]
    for c in chaves:
        b2_put(c, corpo, "text/csv")
    svg = _svg_linha(f"{simbolo} {intervalo} fechamento", fechos, ini, fim).encode("utf-8")
    grafico = f"dados/graficos/{simbolo}_{intervalo}.svg"
    b2_put(grafico, svg, "image/svg+xml")
    var = (fechos[-1] / fechos[0] - 1) * 100 if fechos[0] else 0.0
    return (f"✅ Binance {simbolo}: último {fechos[-1]:g} ({var:+.2f}% em {len(velas)} candles de {intervalo}, "
            f"{ini} → {fim}).\n   • {chaves[0]} ({_bytes_fmt(len(corpo))})\n   • {grafico} ({_bytes_fmt(len(svg))})")


def coletar_bcb(codigo: int, n: int = 20) -> str:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{min(n, 20)}?formato=json"
    st_, txt = _http("GET", url)
    if st_ != 200:
        return f"❌ Banco Central série {codigo}: HTTP {st_} {txt[:120]}"
    dados = json.loads(txt)
    if not isinstance(dados, list) or not dados:
        return f"❌ Banco Central série {codigo}: resposta vazia."
    corpo = json.dumps(dados, ensure_ascii=False, indent=1).encode("utf-8")
    chaves = [f"dados/bcb/sgs_{codigo}_{_agora():%Y%m%d_%H%M}.json", f"dados/bcb/sgs_{codigo}_ultimo.json"]
    for c in chaves:
        b2_put(c, corpo, "application/json")
    nome = BCB_NOMES.get(codigo, f"série {codigo}")
    return (f"✅ Banco Central {nome} (SGS {codigo}): {dados[-1].get('valor')} em {dados[-1].get('data')}.\n"
            f"   • {chaves[0]} ({_bytes_fmt(len(corpo))})")


def _parse_dados(texto: str):
    low = _norm(texto)
    simbolos = []
    for m in re.finditer(r"\b([a-z0-9]{2,8}usdt)\b", low):
        simbolos.append(m.group(1).upper())
    for nome, sim in BIN_ALIAS:
        if re.search(rf"\b{nome}\b", low) and sim not in simbolos:
            simbolos.append(sim)
    series = []
    for nome, cod in BCB_ALIAS:
        if re.search(rf"\b{nome}\b", low) and cod not in series:
            series.append(cod)
            low = re.sub(rf"\b{nome}\b", " ", low)
    for m in re.finditer(r"\b(?:serie|sgs)\s*(\d{1,5})\b", low):
        if int(m.group(1)) not in series:
            series.append(int(m.group(1)))
    intervalo = next((i for i in INTERVALOS if re.search(rf"\b{i}\b", low)), "1h")
    m = re.search(r"\b(\d{2,4})\s*candles?\b", low)
    limite = min(int(m.group(1)), 1000) if m else 500
    tudo = bool(re.search(r"\b(tudo|todos|todas|completo|automatic\w*|mercado)\b", low))
    quer_bin = bool(simbolos or re.search(r"\b(binance|cripto\w*|trade|trading|grafic\w*)\b", low))
    quer_bcb = bool(series or re.search(r"\b(banco central|bcb|juros|inflacao)\b", low))
    if tudo or (quer_bin and not simbolos):
        simbolos = simbolos or ["BTCUSDT", "ETHUSDT"]
    if tudo and not quer_bcb:
        series = series or [432, 433, 1, 12]
        quer_bcb = True
    if quer_bcb and not series:
        series = [432, 433, 1, 12]
    return simbolos if quer_bin or tudo else [], series if quer_bcb else [], intervalo, limite


def _cmd_dados(texto: str) -> str:
    simbolos, series, intervalo, limite = _parse_dados(texto)
    if not simbolos and not series:
        return ("Diga o que baixar. Ex.: 'baixa dados do bitcoin e ethereum da Binance', "
                "'baixa a Selic e o IPCA do Banco Central' ou 'coleta tudo'.")
    linhas = []
    for s in simbolos[:6]:
        try:
            linhas.append(coletar_binance(s, intervalo, limite))
        except Exception as e:
            linhas.append(f"❌ {s}: {_det(e)}")
    for c in series[:6]:
        try:
            linhas.append(coletar_bcb(c))
        except Exception as e:
            linhas.append(f"❌ SGS {c}: {_det(e)}")
    return "📥 Coleta concluída e gravada no cofre B2:\n" + "\n".join(linhas)


# ============================================================
# GITHUB ACTIONS (acionar / status / reativar o robô)
# ============================================================
def _gh_cfg():
    return (_secret("GITHUB_API_KEY") or _secret("GITHUB_TOKEN"), _secret("GITHUB_USER"), _secret("GITHUB_REPO"))


def _gh(metodo, caminho, corpo=None):
    token, _, _ = _gh_cfg()
    return _http(metodo, "https://api.github.com" + caminho, corpo=corpo, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})


def _gh_pronto():
    token, user, repo = _gh_cfg()
    if not (token and user and repo):
        return None, "GitHub não configurado (GITHUB_API_KEY, GITHUB_USER e GITHUB_REPO nos Secrets)."
    return f"/repos/{user}/{repo}", ""


def _workflow(texto: str) -> str:
    m = re.search(r"\b([\w-]+\.ya?ml)\b", texto, re.I)
    if m:
        return m.group(1)
    if re.search(r"telegram", texto, re.I):
        return _secret("GITHUB_WORKFLOW_TELEGRAM") or "zeus_telegram.yml"
    return _secret("GITHUB_WORKFLOW") or "zeus_autonomo.yml"


def gh_acionar(workflow: str) -> str:
    base, erro = _gh_pronto()
    if erro:
        return "❌ " + erro
    ref = _secret("GITHUB_BRANCH")
    if not ref:
        s, txt = _gh("GET", base)
        if s != 200:
            return f"❌ Não consegui ler o repositório (HTTP {s}). Confira GITHUB_USER/GITHUB_REPO e o acesso do token."
        ref = json.loads(txt).get("default_branch", "main")
    s, txt = _gh("POST", f"{base}/actions/workflows/{workflow}/dispatches", {"ref": ref})
    if s == 204:
        return f"✅ Robô acionado: {workflow} na branch {ref}. Peça 'status do robô' em ~1 minuto para ver o resultado."
    dicas = {
        401: "token inválido ou expirado.",
        403: "o token não tem permissão de Actions (fine-grained: 'Actions: Read and write'; clássico: escopo 'workflow').",
        404: f"o arquivo {workflow} não existe em .github/workflows/ da branch {ref}, ou o token não enxerga o repositório.",
        422: f"o {workflow} não tem 'workflow_dispatch:' no 'on:', ou a branch {ref} está errada.",
    }
    return f"❌ GitHub recusou (HTTP {s}): {dicas.get(s, txt[:140])}"


def gh_status(workflow: str, n: int = 3) -> str:
    base, erro = _gh_pronto()
    if erro:
        return "❌ " + erro
    s, txt = _gh("GET", f"{base}/actions/workflows/{workflow}/runs?per_page={n}")
    if s != 200:
        return f"❌ Não consegui ler as execuções de {workflow} (HTTP {s})."
    runs = json.loads(txt).get("workflow_runs", [])
    if not runs:
        return f"Nenhuma execução de {workflow} ainda."
    ic = {"success": "✅", "failure": "❌", "cancelled": "⚪", "skipped": "⚪"}
    linhas = [f"{ic.get(r.get('conclusion') or '', '⏳')} {r.get('status')}/{r.get('conclusion') or 'em andamento'} "
              f"— {str(r.get('created_at', ''))[:16].replace('T', ' ')} UTC ({r.get('event')})" for r in runs[:n]]
    return f"📊 Últimas execuções de {workflow}:\n" + "\n".join(linhas)


def gh_habilitar(workflow: str) -> str:
    base, erro = _gh_pronto()
    if erro:
        return "❌ " + erro
    s, txt = _gh("PUT", f"{base}/actions/workflows/{workflow}/enable")
    return (f"✅ Agendamento de {workflow} reativado." if s == 204
            else f"❌ Não consegui reativar {workflow} (HTTP {s}): {txt[:120]}")


# ============================================================
# TELEGRAM e E-MAIL
# ============================================================
def telegram_enviar(texto: str) -> str:
    token, chat = _secret("TELEGRAM_BOT_TOKEN"), _secret("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "❌ Telegram não configurado (TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets)."
    texto = (texto or "").strip()
    if not texto:
        return "O que devo enviar no Telegram? Ex.: 'manda no telegram: reunião às 15h'."
    s, txt = _http("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                   corpo={"chat_id": chat, "text": texto[:4000]})
    if s == 200:
        return "✅ Mensagem enviada no Telegram."
    return f"❌ Telegram recusou (HTTP {s}): {txt[:140]}"


def _smtp_cfg():
    user = _secret("SMTP_USER")
    return {"host": _secret("SMTP_HOST") or "smtp.gmail.com", "port": int(_secret("SMTP_PORT") or 587),
            "user": user, "pwd": _secret("SMTP_PASS"), "de": _secret("EMAIL_FROM") or user}


def _smtp_abrir(c):
    """Tenta STARTTLS (587) e SSL (465). Retorna conexão autenticada."""
    modos = ["ssl", "tls"] if c["port"] == 465 else ["tls", "ssl"]
    ultimo = None
    for modo in modos:
        try:
            if modo == "ssl":
                s = smtplib.SMTP_SSL(c["host"], 465 if c["port"] == 587 else c["port"], timeout=25,
                                     context=ssl.create_default_context())
            else:
                s = smtplib.SMTP(c["host"], 587 if c["port"] == 465 else c["port"], timeout=25)
                s.starttls(context=ssl.create_default_context())
            s.login(c["user"], c["pwd"])
            return s
        except smtplib.SMTPAuthenticationError:
            raise
        except Exception as e:
            ultimo = e
    raise ultimo


def _dica_smtp(e) -> str:
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("login recusado. No Gmail use uma SENHA DE APP (Conta Google > Segurança > Senhas de app), "
                "não a senha normal, e SMTP_USER = e-mail completo.")
    return "servidor/porta inacessível. Gmail: smtp.gmail.com porta 587."


def enviar_email(para: str, assunto: str, corpo: str) -> str:
    c = _smtp_cfg()
    if not c["user"] or not c["pwd"]:
        return "❌ E-mail não configurado (SMTP_USER e SMTP_PASS nos Secrets; opcionais SMTP_HOST, SMTP_PORT, EMAIL_FROM)."
    if not re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", para or ""):
        return "❌ Destinatário inválido."
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto or "Mensagem do Zeus", c["de"], para
    msg.set_content(corpo or "")
    try:
        s = _smtp_abrir(c)
        try:
            s.send_message(msg)
        finally:
            with contextlib.suppress(Exception):
                s.quit()
        return f"✅ E-mail enviado para {para}."
    except Exception as e:
        return f"❌ E-mail não enviado: {type(e).__name__}. {_dica_smtp(e)}"


_RE_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"


def _cmd_email(texto: str) -> str:
    achados = re.findall(_RE_EMAIL, texto)
    para = achados[0] if achados else ""
    if not para and re.search(r"\b(pra|para|pro|ao)\s+(mim|eu|me|meu)\b", texto, re.I):
        para = _secret("EMAIL_TO") or _smtp_cfg()["user"]
    if not para:
        return ("Para quem envio? Ex.: 'envia email para alguem@dominio.com assunto Reunião corpo Confirmada às 15h' "
                "ou 'manda um email pra mim assunto Teste corpo Funcionou'.")
    m_ass = re.search(r"assunto\s*[:\-]?\s*(.+?)(?=\s+(?:corpo|mensagem|texto|dizendo|conte[uú]do)\b|\n|$)", texto, re.I | re.S)
    m_cor = re.search(r"(?:corpo|mensagem|texto|dizendo|conte[uú]do)\s*[:\-]?\s*(.+)$", texto, re.I | re.S)
    assunto = m_ass.group(1).strip(" .,;:") if m_ass else ""
    corpo = m_cor.group(1).strip() if m_cor else ""
    if not corpo and not assunto:
        resto = texto.split(para, 1)[-1].strip(" .,;:-")
        corpo = resto
    if not corpo:
        corpo = assunto or "Mensagem enviada pelo Zeus."
    return enviar_email(para, assunto or "Mensagem do Zeus", corpo)


def _cmd_telegram(texto: str) -> str:
    t = re.sub(r"(?is)^.*?\b(telegram|tg)\b\s*[:\-]?\s*(?:dizendo|escrevendo|mensagem)?\s*[:\-]?\s*", "", texto, count=1).strip()
    if not t:   # ordem invertida: "manda 'oi' no telegram"
        t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|envia|enviar|mande)\b|\b(no|pelo|via)\s+(telegram|tg)\b", "", texto).strip(" .,:-")
    return telegram_enviar(t)


# ============================================================
# SPOTIFY (tocar, pausar, próxima, volume) + MENU DE VOZ
# ============================================================
def _sp():
    """Cliente spotipy autenticado (token em cache na sessão). Lança RuntimeError com mensagem clara."""
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        raise RuntimeError("Falta o pacote 'spotipy' no requirements.txt.")
    cid, sec, ref = _secret("SPOTIFY_CLIENT_ID"), _secret("SPOTIFY_CLIENT_SECRET"), _secret("SPOTIFY_REFRESH_TOKEN")
    if not (cid and sec and ref):
        raise RuntimeError("Faltam SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET e SPOTIFY_REFRESH_TOKEN nos Secrets.")
    cache = st.session_state.get("_za_sp") or {}
    if not cache.get("tok") or cache.get("exp", 0) < time.time() + 60:
        auth = SpotifyOAuth(client_id=cid, client_secret=sec,
                            redirect_uri=_secret("SPOTIFY_REDIRECT_URI") or "http://127.0.0.1:8888/callback",
                            scope="user-modify-playback-state user-read-playback-state", cache_path=None)
        info = auth.refresh_access_token(ref)
        cache = {"tok": info["access_token"], "exp": info.get("expires_at", time.time() + 3000)}
        st.session_state["_za_sp"] = cache
    return spotipy.Spotify(auth=cache["tok"])


def _sp_erro(e: Exception) -> str:
    cod = getattr(e, "http_status", None)
    if isinstance(e, RuntimeError):
        return "❌ " + str(e)
    if cod == 404:
        return "❌ Nenhum aparelho ativo: abra o Spotify no celular ou PC e dê play em qualquer faixa uma vez."
    if cod == 403:
        return "❌ O Spotify recusou: controlar o player exige conta Premium."
    if cod == 401:
        return "❌ Token do Spotify inválido: gere um novo SPOTIFY_REFRESH_TOKEN."
    return f"❌ Spotify: {type(e).__name__} {str(e)[:100]}"


def _sp_dispositivo(sp):
    devs = sp.devices().get("devices", [])
    for d in devs:
        if d.get("is_active"):
            return d["id"]
    return devs[0]["id"] if devs else None


def sp_tocar(nome: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        return "Digite o nome da música ou artista."
    try:
        sp = _sp()
        itens = sp.search(q=nome, limit=1, type="track").get("tracks", {}).get("items", [])
        if not itens:
            return f"Nenhuma faixa encontrada para '{nome}'."
        dev = _sp_dispositivo(sp)
        if not dev:
            return "❌ Nenhum aparelho do Spotify disponível: abra o app do Spotify e tente de novo."
        sp.start_playback(device_id=dev, uris=[itens[0]["uri"]])
        art = itens[0]["artists"][0]["name"] if itens[0].get("artists") else "?"
        return f"🎵 Tocando: {itens[0]['name']} — {art}"
    except Exception as e:
        return _sp_erro(e)


def sp_controle(acao: str) -> str:
    try:
        sp = _sp()
        if acao == "pausar":
            sp.pause_playback()
            return "⏸ Pausado."
        if acao == "retomar":
            sp.start_playback()
            return "▶ Retomado."
        if acao == "alternar":
            pb = sp.current_playback()
            if pb and pb.get("is_playing"):
                sp.pause_playback()
                return "⏸ Pausado."
            sp.start_playback()
            return "▶ Retomado."
        if acao == "proxima":
            sp.next_track()
            return "⏭ Próxima faixa."
        if acao == "anterior":
            sp.previous_track()
            return "⏮ Faixa anterior."
        if acao == "agora":
            pb = sp.current_playback()
            if not pb or not pb.get("item"):
                return "Nada tocando agora."
            it = pb["item"]
            vol = (pb.get("device") or {}).get("volume_percent")
            return (f"{'▶' if pb.get('is_playing') else '⏸'} {it['name']} — {it['artists'][0]['name']}"
                    + (f" · volume {vol}%" if vol is not None else ""))
        return "Ação desconhecida."
    except Exception as e:
        return _sp_erro(e)


def sp_volume(pct=None, delta=None) -> str:
    try:
        sp = _sp()
        if pct is None:
            pb = sp.current_playback()
            atual = ((pb or {}).get("device") or {}).get("volume_percent", 50)
            pct = atual + (delta or 0)
        pct = int(max(0, min(100, pct)))
        sp.volume(pct)
        return f"🔊 Volume do Spotify: {pct}%"
    except Exception as e:
        return _sp_erro(e)


def _cb_sp_volume():
    st.session_state["za_sp_msg"] = sp_volume(pct=st.session_state.get("za_sp_vol", 50))


def _vozes():
    fn = _G.get("listar_vozes_edge")
    try:
        return fn() if callable(fn) else {}
    except Exception:
        return {}


def _ajustar(chave, delta, minimo, maximo, casas=2):
    """Altera o controle de voz ORIGINAL (mesma chave do menu lateral) dentro de um callback."""
    atual = st.session_state.get(chave)
    if atual is None:
        return
    st.session_state[chave] = round(max(minimo, min(maximo, atual + delta)), casas)


def _cb_voz_trocar(passo):
    nomes = list(_vozes().keys())
    if not nomes:
        return
    atual = st.session_state.get("voz_sel", nomes[0])
    i = nomes.index(atual) if atual in nomes else 0
    st.session_state["voz_sel"] = nomes[(i + passo) % len(nomes)]


def render_spotify_voz():
    """Menu lateral: Spotify (tocar, ⏮ ⏯ ⏭, volume) + Voz do Zeus (voz, velocidade, tom, volume)."""
    with st.sidebar:
        st.divider()
        with st.expander("🎵 Spotify e 🔊 Voz", expanded=True):
            st.markdown("**🎵 Spotify**")
            st.text_input("Música ou artista", key="za_sp_busca")
            if st.button("▶ Tocar no Spotify", key="za_sp_tocar", use_container_width=True):
                st.session_state["za_sp_msg"] = sp_tocar(st.session_state.get("za_sp_busca", ""))
            c1, c2, c3 = st.columns(3)
            if c1.button("⏮", key="za_sp_ant", use_container_width=True):
                st.session_state["za_sp_msg"] = sp_controle("anterior")
            if c2.button("⏯", key="za_sp_pp", use_container_width=True):
                st.session_state["za_sp_msg"] = sp_controle("alternar")
            if c3.button("⏭", key="za_sp_prox", use_container_width=True):
                st.session_state["za_sp_msg"] = sp_controle("proxima")
            st.slider("🔊 Volume do Spotify", 0, 100, 50, 5, key="za_sp_vol", on_change=_cb_sp_volume)
            if st.button("🔄 O que está tocando", key="za_sp_agora", use_container_width=True):
                st.session_state["za_sp_msg"] = sp_controle("agora")
            if st.session_state.get("za_sp_msg"):
                st.caption(st.session_state["za_sp_msg"])

            st.markdown("**🔊 Voz do Zeus**")
            nome_voz = st.session_state.get("voz_sel", "(padrão)")
            st.caption(f"Voz: {nome_voz}")
            v1, v2 = st.columns(2)
            v1.button("◀ voz", key="za_v_ant", on_click=_cb_voz_trocar, args=(-1,), use_container_width=True)
            v2.button("voz ▶", key="za_v_prox", on_click=_cb_voz_trocar, args=(1,), use_container_width=True)
            for rotulo, chave, passo, mn, mx, fmt in (
                    ("Velocidade", "rate_sel", 0.05, 0.8, 1.5, "{:.2f}x"),
                    ("Tom", "tom_sel", 5, -50, 50, "{:+d} Hz"),
                    ("Volume da voz", "vol_sel", 5, -50, 50, "{:+d}%")):
                atual = st.session_state.get(chave)
                a, b, c = st.columns([1, 2, 1])
                a.button("−", key=f"za_{chave}_m", on_click=_ajustar, args=(chave, -passo, mn, mx), use_container_width=True)
                b.markdown(f"<div style='text-align:center'>{rotulo}<br><b>{fmt.format(atual) if atual is not None else '—'}</b></div>",
                           unsafe_allow_html=True)
                c.button("+", key=f"za_{chave}_p", on_click=_ajustar, args=(chave, passo, mn, mx), use_container_width=True)
            if st.button("🔊 Testar voz", key="za_v_teste", use_container_width=True):
                sint, vozes = _G.get("sintetizar"), _vozes()
                voz_id = vozes.get(st.session_state.get("voz_sel", ""), "pt-BR-FranciscaNeural")
                mp3 = sint("Olá, eu sou o Zeus. Como posso ajudar?", voz_id, st.session_state.get("rate_sel", 1.0)) if sint else None
                if mp3:
                    st.audio(mp3, format="audio/mp3")
                else:
                    st.warning("Não consegui gerar a voz agora.")


# ============================================================
# MEMÓRIA QUE APRENDE (persistida no cofre B2: memoria/fatos.json)
# ============================================================
MEM_KEY = "memoria/fatos.json"


def _mem_ler(forcar=False):
    cache = st.session_state.get("_za_mem")
    if cache and not forcar and time.time() - cache["ts"] < 120:
        return cache["fatos"]
    try:
        fatos = json.loads(b2_get(MEM_KEY).decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception:
        return (cache or {}).get("fatos", [])      # falha real: usa o que já tinha, nunca regrava às cegas
    st.session_state["_za_mem"] = {"ts": time.time(), "fatos": fatos}
    return fatos


def mem_ensinar(pergunta: str, resposta: str) -> str:
    try:
        fatos = json.loads(b2_get(MEM_KEY).decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception as e:
        return f"❌ Não consegui ler a memória (não vou sobrescrever): {_det(e)}"
    fatos.append({"p": pergunta.strip(), "r": resposta.strip(), "ts": _agora().isoformat(timespec="seconds")})
    try:
        b2_put(MEM_KEY, json.dumps(fatos[-500:], ensure_ascii=False, indent=1).encode("utf-8"), "application/json")
    except Exception as e:
        return f"❌ Não consegui gravar a memória: {_det(e)}"
    st.session_state["_za_mem"] = {"ts": time.time(), "fatos": fatos[-500:]}
    return f"🧠 Aprendido e guardado no cofre ({len(fatos)} fatos na memória)."


_STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "que", "eh", "me", "se", "no", "na"}


def _sim(a: str, b: str) -> float:
    a = " ".join(w for w in _norm(a).split() if w not in _STOP)
    b = " ".join(w for w in _norm(b).split() if w not in _STOP)
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return 0.5 * len(ta & tb) / len(ta | tb) + 0.5 * SequenceMatcher(None, a, b).ratio()


def mem_resposta_direta(pergunta: str):
    melhor, s_melhor = None, 0.0
    for f in _mem_ler():
        if f.get("p") == f.get("r"):
            continue            # fato solto, não é par pergunta/resposta
        s = _sim(pergunta, f.get("p", ""))
        if s > s_melhor:
            melhor, s_melhor = f, s
    return melhor["r"] if melhor and s_melhor >= 0.88 else None


def mem_contexto(pergunta: str, n: int = 3) -> str:
    fatos = _mem_ler()
    if not fatos:
        return ""
    pontuados = sorted(((_sim(pergunta, f.get("p", "")), f) for f in fatos), key=lambda x: x[0], reverse=True)
    uteis = [f for s, f in pontuados[:n] if s >= 0.2]
    uteis += [f for f in fatos[-2:] if f not in uteis]      # preferências recentes sempre contam
    if not uteis:
        return ""
    return ("[MEMÓRIA ENSINADA PELO CRIADOR — priorize isto sobre conhecimento genérico]\n"
            + "\n".join(f"- {f['p']}" + (f" => {f['r']}" if f["r"] != f["p"] else "") for f in uteis[:n + 2]))


def _cmd_aprender(texto: str) -> str:
    corpo = re.sub(r"(?i)^\s*(zeus[,:\s]+)?(aprenda|aprende|memorize|lembre-se|lembre)\s*(que|:)?\s*", "", texto).strip()
    if not corpo:
        return "O que devo aprender? Ex.: 'aprenda: qual meu time => Palmeiras' ou 'lembre que respondo melhor com exemplos'."
    m = re.split(r"\s*(?:=>|->|=)\s*", corpo, maxsplit=1)
    if len(m) == 2 and m[0] and m[1]:
        return mem_ensinar(m[0], m[1])
    return mem_ensinar(corpo, corpo)


def _cmd_memoria_listar(_t: str) -> str:
    fatos = _mem_ler(forcar=True)
    if not fatos:
        return "Minha memória está vazia. Ensine com: 'aprenda: pergunta => resposta'."
    return f"🧠 Tenho {len(fatos)} fatos. Os últimos:\n" + "\n".join(
        f"- {f['p']}" + (f" => {f['r']}" if f['r'] != f['p'] else "") for f in fatos[-10:])


# ============================================================
# COMANDOS DO COFRE EM LINGUAGEM NATURAL
# ============================================================
_RE_PATH = r"([\w.\-]+(?:/[\w.\-]+)+/?|[\w\-]+\.\w{2,5})"


def _path_do_texto(texto: str) -> str:
    for m in re.finditer(_RE_PATH, texto):
        cand = m.group(1)
        if "@" in texto[max(0, m.start() - 1):m.start()] or re.fullmatch(r"\d+\.\d+", cand):
            continue
        return cand.strip(".,;:").lstrip("/")
    return ""


def _cmd_cofre_listar(texto: str) -> str:
    prefixo = _path_do_texto(texto) if "/" in texto else ""
    try:
        itens = b2_list(prefixo, 40)
    except Exception as e:
        return f"❌ Não consegui listar o cofre: {_det(e)}"
    if not itens:
        return f"Cofre vazio{' em ' + prefixo if prefixo else ''} (0 arquivos)."
    return f"📦 Cofre B2 {prefixo or '(raiz)'} — {len(itens)} arquivo(s):\n" + "\n".join(
        f"- {k} ({_bytes_fmt(s)})" for k, s in itens)


def _cmd_cofre_ler(texto: str) -> str:
    chave = _path_do_texto(texto)
    if not chave:
        return "Qual arquivo? Ex.: 'ler dados/bcb/sgs_432_ultimo.json do cofre'."
    dados = None
    for c in [chave] + (["cofre/" + chave] if "/" not in chave else []):
        try:
            dados, chave = b2_get(c), c
            break
        except FileNotFoundError:
            continue
        except Exception as e:
            return f"❌ Não consegui ler {c}: {_det(e)}"
    if dados is None:
        return f"❌ {chave} não existe no cofre. Peça 'lista arquivos do cofre' para ver os nomes."
    txt = dados[:3000].decode("utf-8", "replace")
    return f"📄 {chave} ({_bytes_fmt(len(dados))}):\n{txt}" + ("\n…(cortado)" if len(dados) > 3000 else "")


def _cmd_cofre_apagar(texto: str) -> str:
    chave = _path_do_texto(texto)
    if not chave:
        return "Qual arquivo apagar? Diga o caminho exato. Ex.: 'apaga dados/binance/teste.csv do cofre'."
    if chave in PROTEGIDOS:
        return f"🛡️ {chave} é um arquivo de sistema do Zeus e está protegido contra apagar pelo chat."
    candidatos = [chave] + (["cofre/" + chave] if "/" not in chave else [])
    for c in candidatos:
        try:
            b2_delete(c)
            return f"🗑️ {c} apagado do cofre."
        except FileNotFoundError:
            continue
        except Exception as e:
            return f"❌ Não consegui apagar {c}: {_det(e)}"
    return f"❌ {chave} não existe no cofre (nada foi apagado)."


def _cmd_cofre_salvar(texto: str) -> str:
    m = re.search(r"(?:como|chamad[oa]|nome)\s+([\w.\-/]+)", texto, re.I)
    nome = (m.group(1) if m else "") or _path_do_texto(texto)
    c = re.search(r"(?:dizendo|contendo|conte[uú]do|texto|:)\s*(.+)$", texto, re.I | re.S)
    conteudo = c.group(1).strip() if c else ""
    if not conteudo:
        return "O que devo salvar? Ex.: 'salva no cofre como notas/ideia.txt: texto da ideia'."
    nome = nome.lstrip("/") or f"cofre/nota_{_agora():%Y%m%d_%H%M%S}.txt"
    if "/" not in nome:
        nome = "cofre/" + nome
    if nome in PROTEGIDOS:
        return f"🛡️ {nome} é protegido."
    try:
        b2_put(nome, conteudo.encode("utf-8"), "text/plain; charset=utf-8")
        return f"✅ Salvo no cofre: {nome} ({_bytes_fmt(len(conteudo.encode('utf-8')))})."
    except Exception as e:
        return f"❌ Não consegui salvar: {_det(e)}"


# ============================================================
# DIAGNÓSTICO (mostra exatamente o que está falhando)
# ============================================================
def diagnostico(foco: str = "tudo", enviar_teste: bool = False) -> str:
    L = []
    quer = lambda k: foco in ("tudo", k)
    if quer("cofre"):
        try:
            b2_put("diagnostico/_teste.txt", b"ok", "text/plain")
            ok = b2_get("diagnostico/_teste.txt") == b"ok"
            b2_delete("diagnostico/_teste.txt")
            L.append("✅ Cofre B2: gravou, leu e apagou um arquivo de teste." if ok else "❌ Cofre B2: leitura diferente da gravação.")
        except Exception as e:
            L.append(f"❌ Cofre B2: {_det(e)}")
    if quer("github"):
        base, erro = _gh_pronto()
        if erro:
            L.append("❌ GitHub: " + erro)
        else:
            s, txt = _gh("GET", base)
            if s != 200:
                L.append(f"❌ GitHub: não leu o repositório (HTTP {s}).")
            else:
                wf = _workflow("")
                s2, _ = _gh("GET", f"{base}/actions/workflows/{wf}")
                L.append(f"✅ GitHub: repositório ok; workflow {wf} " + ("encontrado." if s2 == 200
                         else f"NÃO encontrado (HTTP {s2}): o arquivo precisa estar em .github/workflows/."))
    if quer("telegram"):
        tok = _secret("TELEGRAM_BOT_TOKEN")
        if not tok:
            L.append("❌ Telegram: TELEGRAM_BOT_TOKEN ausente.")
        else:
            s, txt = _http("GET", f"https://api.telegram.org/bot{tok}/getMe")
            L.append("✅ Telegram: bot válido." if s == 200 else f"❌ Telegram: token recusado (HTTP {s}).")
            if enviar_teste and s == 200:
                L.append(telegram_enviar("✅ Teste do Zeus: o Telegram está funcionando."))
    if quer("email"):
        c = _smtp_cfg()
        if not c["user"] or not c["pwd"]:
            L.append("❌ E-mail: SMTP_USER / SMTP_PASS ausentes nos Secrets.")
        else:
            try:
                s = _smtp_abrir(c)
                with contextlib.suppress(Exception):
                    s.quit()
                L.append(f"✅ E-mail: login ok em {c['host']} como {c['user']}.")
                if enviar_teste:
                    L.append(enviar_email(_secret("EMAIL_TO") or c["user"], "Teste do Zeus", "✅ O e-mail do Zeus está funcionando."))
            except Exception as e:
                L.append(f"❌ E-mail: {type(e).__name__}. {_dica_smtp(e)}")
    if quer("spotify"):
        try:
            n = len(_sp().devices().get("devices", []))
            L.append(f"✅ Spotify: autenticado; {n} aparelho(s) visível(is)" + ("." if n else " (abra o app do Spotify)."))
        except Exception as e:
            L.append(_sp_erro(e))
    return "🔧 Diagnóstico do Zeus:\n" + "\n".join(L)


# ============================================================
# ROTEADOR + CHAT NORMAL
# ============================================================
def _pos(low, padrao):
    m = re.search(padrao, low)
    return m.start() if m else None


def _tem(low, *padroes):
    return all(re.search(p, low) for p in padroes)


def _rotear(t: str):
    """Retorna a resposta pronta, ou None se não for um comando."""
    low = _norm(t)
    pergunta = "?" in t or bool(re.match(r"\s*(como|qual|quais|o que|por que|porque|quando|onde|quem)\b", low))
    topo_cofre = r"\b(cofre|bucket|backblaze|blaze\w*|b2|nuvem)\b"
    path = _path_do_texto(t)

    if re.match(r"\s*(zeus[,:\s]+)?(faz\w*\s+(um\s+)?)?(diagnostic\w*|testa\w*|testar|teste\s+(de|do|da)|verifica\w*|checa\w*)\b", low) \
            and re.search(r"\b(conex\w+|e mail|email|telegram|cofre|b2|github|spotify|tudo|sistema|integra\w+)\b", low) \
            and not re.search(r"\b(assunto|corpo|dizendo)\b", low) and len(low.split()) <= 12:
        for foco, pad in (("email", r"\be mail\b|\bemail\b"), ("telegram", r"\btelegram\b"), ("cofre", topo_cofre),
                          ("github", r"\bgithub\b|\brobo\b"), ("spotify", r"\bspotify\b")):
            if re.search(pad, low):
                return diagnostico(foco, enviar_teste=foco in ("email", "telegram"))
        return diagnostico("tudo")

    # Spotify
    if not pergunta and re.search(r"\bvolume\b", low) and re.search(r"\d", low) and re.search(r"\b(spotify|musica|som)\b|^\s*(zeus\s+)?volume", low):
        return sp_volume(pct=int(re.search(r"(\d{1,3})", low).group(1)))
    if not pergunta and re.search(r"\b(aument\w+|sobe|suba|abaix\w+|diminu\w+|baixa)\b.*\bvolume\b", low) and re.search(r"\b(spotify|musica|som)\b", low):
        return sp_volume(delta=10 if re.search(r"\b(aument\w+|sobe|suba)\b", low) else -10)
    if not pergunta and (re.match(r"\s*(zeus[,:\s]+)?(paus\w+|pare|para)\s+(a\s+)?(musica|spotify|som)\b", low)
                         or low.strip() in ("pausa", "pausar")):
        return sp_controle("pausar")
    if not pergunta and re.search(r"\b(retom\w+|continu\w+|despaus\w+)\b.*\b(musica|spotify)\b", low):
        return sp_controle("retomar")
    if not pergunta and re.search(r"\b(proxima|pula|avanca)\b", low) and re.search(r"\b(musica|faixa|spotify)\b", low):
        return sp_controle("proxima")
    if not pergunta and re.search(r"\b(anterior|volta)\b", low) and re.search(r"\b(musica|faixa)\b", low):
        return sp_controle("anterior")
    if re.search(r"\b(que|qual)\b.*\b(tocando|musica)\b.*\b(agora|tocando)\b", low):
        return sp_controle("agora")
    m = re.match(r"\s*(?:zeus[,:\s]+)?(?:toca|tocar|toque|poe|põe|coloca)\s+(?:a\s+)?(?:musica\s+)?(.+)$", t, re.I)
    if m and not pergunta:
        return sp_tocar(re.sub(r"(?i)\s*(no|pelo)\s+spotify\s*$", "", m.group(1)).strip(" .,!?"))

    # Robô do GitHub
    if re.search(r"\b(reativ\w+|habilit\w+)\b", low) and re.search(r"\b(robo\w*|action|workflow|agendamento)\b", low):
        return gh_habilitar(_workflow(t))
    if re.search(r"\b(status|situacao|como esta|ultim\w+ (?:execu\w+|rodada)|funcionou)\b", low) and re.search(r"\b(robo\w*|action|workflow)\b", low):
        return gh_status(_workflow(t))
    if re.search(r"\b(aciona\w*|roda|rode|rodar|execut\w+|inicia\w*|liga\w*|chama\w*|ativa\w*|dispara\w*)\b", low) \
            and re.search(r"\b(robo\w*|action|workflow)\b", low):
        return gh_acionar(_workflow(t))

    # Cofre B2: apagar / salvar / ler / listar (antes de dados, para "apaga" não virar download)
    if re.search(r"\b(apag\w+|delet\w+|remov\w+|exclu\w+)\b", low) and path and (re.search(topo_cofre, low) or "/" in path):
        return _cmd_cofre_apagar(t)
    if re.search(r"\b(salv\w+|guard\w+|grav\w+)\b", low) and re.search(topo_cofre, low) \
            and not re.search(r"\b(binance|banco central|bcb|selic|ipca|cripto\w*)\b", low):
        return _cmd_cofre_salvar(t)

    # Dados de mercado
    topico = r"\b(binance|cripto\w*|bitcoin|btc|ethereum|eth|banco central|bcb|selic|ipca|dolar|cdi|mercado|trade|trading|grafic\w+)\b"
    if not pergunta and re.search(topico, low) and re.search(r"\b(baix\w+|colet\w+|pega\w*|traz\w*|atualiz\w+)\b", low):
        return _cmd_dados(t)
    if not pergunta and re.search(r"\b(coleta\w*|colet\w+)\b.*\b(tudo|dados|automatic\w+)\b", low):
        return _cmd_dados(t + " tudo")

    if re.search(r"\b(le|ler|abr\w+|mostr\w+)\b", low) and path and "/" in path and re.search(r"\.\w{2,5}$", path):
        return _cmd_cofre_ler(t)
    if re.search(r"\b(list\w+|mostr\w+|ver|quais|o que (?:tem|ha))\b", low) and re.search(topo_cofre, low):
        return _cmd_cofre_listar(t)

    # Aprendizado / memória
    if re.match(r"\s*(zeus[,:\s]+)?(aprenda|aprende|memorize|lembre-se|lembre)\s*(que|:)", t, re.I):
        return _cmd_aprender(t)
    if re.search(r"\b(o que (?:voce )?aprendeu|sua memoria|mostre a memoria)\b", low):
        return _cmd_memoria_listar(t)

    # Comunicação (vence o que aparecer primeiro na frase)
    pt, pe = _pos(low, r"\b(telegram|tg)\b"), _pos(low, r"\b(e mail|email)\b")
    verbo = re.search(r"\b(envi\w+|mand\w+|dispar\w+)\b", low) or low.startswith(("telegram", "email", "e mail"))
    if not pergunta and verbo and (pt is not None or pe is not None):
        if pe is not None and (pt is None or pe < pt):
            return _cmd_email(t)
        return _cmd_telegram(t)

    # Desenvolver sozinho: habilidades e imagens
    if not pergunta and re.search(r"\b(cri\w+|fa[cz]\w*|desenvolv\w+|aprend\w+ a)\b", low) and re.search(r"\b(habilidade|funcao|script|modulo)\b|\baprend\w+ a\b", low):
        fn = _G.get("zf_aprender_ensemble")
        if not fn:
            return "❌ O módulo de aprendizado (zf_aprender_ensemble) não está no app."
        pedido = re.sub(r"(?i)\b(zeus|cria\w*|faca|faça|desenvolv\w+|uma?|habilidade|fun[cç][aã]o|script|m[oó]dulo|por favor)\b", " ", t)
        pedido = re.sub(r"\s+", " ", pedido).strip(" .,!?:-")
        if not pedido:
            return "Me diga o que a habilidade deve fazer. Ex.: 'Zeus, cria uma habilidade que calcula o IMC'."
        return "⚡ Criando habilidade...\n\n" + fn(pedido, "python")
    if not pergunta and re.search(r"\b(cri\w+|ger\w+|faz\w*)\b", low) and re.search(r"\b(imagem|foto|avatar|ilustracao)\b", low):
        fn = _G.get("zf_gerar_imagem_aprimorada") or _G.get("make_image")
        if not fn:
            return "❌ Módulo de imagem não encontrado no app."
        pedido = re.sub(r"(?i)\b(zeus|cria\w*|gera\w*|faz\w*|uma?|imagem|foto|avatar|ilustra[cç][aã]o|por favor)\b", " ", t)
        pedido = re.sub(r"\s+", " ", pedido).strip(" .,!?:-")
        if not pedido:
            return "Descreva a imagem. Ex.: 'Zeus, cria uma imagem de um dragão de cristal'."
        log = []
        img = fn(pedido, log)
        if img:
            st.session_state["rt_imagem_gerada"] = img
            return f"🎨 Imagem gerada: \"{pedido}\". Ela aparece logo abaixo do painel."
        return "❌ Não consegui gerar a imagem agora. " + " | ".join(log[-2:])

    # Comandos antigos do cofre continuam valendo (b2_salvar:, b2_apagar:, b2_listar)
    return None


def _registrar(prompt, resposta):
    g, ss = _G, st.session_state
    sid = ss.session_id
    ss.messages.append({"role": "user", "content": prompt})
    ss.messages.append({"role": "assistant", "content": resposta})
    try:
        g["add_message"](sid, "user", prompt)
        g["add_message"](sid, "assistant", resposta)
    except Exception:
        pass


def _chat_normal(prompt: str) -> str:
    g, ss = _G, st.session_state
    sid = ss.session_id
    escolhido = ss.get("model_sel", "groq")
    ss.messages.append({"role": "user", "content": prompt})

    direta = mem_resposta_direta(prompt)
    if direta:
        ss.messages.append({"role": "assistant", "content": direta})
        g["add_message"](sid, "user", prompt)
        g["add_message"](sid, "assistant", direta)
        ss.last_info = "🧠 Respondido pela memória que você me ensinou"
        return direta

    prompt_llm, fonte_web = g["enriquecer_com_web"](prompt)
    ctx = mem_contexto(prompt)
    base_sys = g.get("SYS_PROMPT", "")
    if ctx:
        g["SYS_PROMPT"] = base_sys + "\n\n" + ctx
    try:
        ok, resp, prov, erros = g["get_llm_response"](sid, prompt_llm, escolhido)
    finally:
        g["SYS_PROMPT"] = base_sys

    if not ok:   # nenhuma IA respondeu: busca na web por conta própria
        ctx_web, fonte = "", ""
        for _ in range(2):
            ctx_web, fonte = g["web_search"](prompt[:500])
            if ctx_web and ctx_web.strip():
                break
            time.sleep(0.8)
        if ctx_web and ctx_web.strip():
            resp = ("📡 As IAs não responderam. Busquei na internet por conta própria:\n\n"
                    + ctx_web[:3800] + f"\n\n(Fonte: {fonte})")
            ok, prov, fonte_web = True, "Web Search", fonte
        else:
            resp = ("Não consegui contatar as IAs nem obter resultado útil na web agora. "
                    "Tente reformular ou veja o status das chaves no menu lateral.")
            prov = "sem resposta"
            ss.last_info = "⚠️ IAs e web indisponíveis no momento"
    g["add_message"](sid, "user", prompt)
    ss.messages.append({"role": "assistant", "content": resp})
    g["add_message"](sid, "assistant", resp)
    if ok:
        ss.last_info = (f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "")
                        + (" · 🌐 web: " + fonte_web if fonte_web else ""))
    return resp


def processar_prompt(prompt: str) -> str:
    """Substitui o processar_prompt do app: primeiro comandos de ação, depois chat normal."""
    t = (prompt or "").strip()
    if not t:
        return ""
    try:
        resposta = _rotear(t)
    except Exception as e:
        resposta = f"❌ Erro ao executar o comando: {type(e).__name__}: {str(e)[:150]}"
    if resposta is not None:
        _registrar(t, resposta)
        st.session_state.last_info = "⚡ Comando executado"
        return resposta
    return _chat_normal(t)


def _detalhe_erro_corrigido(e: Exception) -> str:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error") or {}
    base = f"{type(e).__name__}: {e}"
    if err.get("Code") or err.get("Message"):
        return f"{base} [{err.get('Code', '')} - {err.get('Message', '')}]"
    return base


def instalar(g: dict):
    """Chame uma vez por execução, ANTES do trecho 'APP PRINCIPAL' (init_db())."""
    global _G
    _G = g                                                  # os globals REAIS do app (não uma cópia)
    g["_detalhe_erro"] = _detalhe_erro_corrigido           # corrige type(e)._name_ se vier da colagem
    original = g.get("add_message")
    if original and not getattr(original, "_za_seguro", False):
        def add_message_seguro(*a, **k):
            try:
                return original(*a, **k)
            except Exception:
                return None
        add_message_seguro._za_seguro = True
        g["add_message"] = add_message_seguro
    return processar_prompt
