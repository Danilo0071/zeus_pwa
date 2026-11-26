import os
import json
import time
import hmac
import hashlib
import base64
import math
import urllib.parse
import re
import html
import threading
import requests

# ================= CONFIGURAÇÕES =================
REPO_PADRAO = os.getenv("GITHUB_REPO", "Danilo0071/zeus_pwa").strip()
BRANCH_PADRAO = os.getenv("GITHUB_BRANCH", "main").strip()

# B2 (API NATIVA - MANTIDO COMO JÁ CONECTA)
B2_KEY_ID = os.getenv("B2_KEY_ID", "").strip().strip('"')
B2_APP_KEY = os.getenv("B2_APPLICATION_KEY", os.getenv("B2_APP_KEY", "")).strip().strip('"')
B2_BUCKET = os.getenv("B2_BUCKET", os.getenv("B2_BUCKET_NAME", "")).strip().strip('"')

# Binance Demo
BINANCE_KEY = os.getenv("BINANCE_API_KEY", os.getenv("BINANCE_KEY", "")).strip().strip('"')
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", os.getenv("BINANCE_SECRET", "")).strip().strip('"')
BINANCE_USE_DEMO = os.getenv("BINANCE_USE_DEMO", "true").lower() == "true"
BINANCE_MAX_LEVERAGE = min(float(os.getenv("BINANCE_MAX_LEVERAGE", "2")), 2.0)
BINANCE_BASE_URL = "https://demo-fapi.binance.com" if BINANCE_USE_DEMO else "https://fapi.binance.com"
BINANCE_SYMBOL = os.getenv("BINANCE_SYMBOL", "BTCUSDT").strip().strip('"')
BINANCE_MARGEM_PCT = float(os.getenv("BINANCE_MARGEM_PCT", "0.02"))  # 2% do saldo por ordem (valores menores, mais trades)
BINANCE_MIN_NOTIONAL = float(os.getenv("BINANCE_MIN_NOTIONAL", "100"))  # mínimo da Binance p/ BTCUSDT
BINANCE_QTY_DECIMALS = int(os.getenv("BINANCE_QTY_DECIMALS", "3"))
TESTE_AUTO_DEMO = os.getenv("TESTE_AUTO_DEMO", "false").lower() == "true"

# ---- Critérios de entrada / risco (novos, todos ajustáveis por variável de ambiente) ----
TAXA_TAKER_PCT = float(os.getenv("TAXA_TAKER_PCT", "0.05"))        # taxa por lado (%)
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.02"))            # folga de slippage por lado (%)
FATOR_TAXA_MIN = float(os.getenv("FATOR_TAXA_MIN", "3"))           # alvo precisa ser >= N x custo ida+volta
SCORE_MIN = int(os.getenv("SCORE_MIN", "2"))                       # força mínima do sinal técnico (0..4)
SCORE_FORTE = int(os.getenv("SCORE_FORTE", "3"))
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.08"))              # mercado "morto" abaixo disso
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", "0.4"))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", "1.5"))
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", "1.5"))
ALVO_RR = float(os.getenv("ALVO_RR", "1.5"))                       # take profit = stop x RR
RISCO_POR_TRADE_PCT = float(os.getenv("RISCO_POR_TRADE_PCT", "0.25"))  # % do saldo arriscado se o stop bater
COOLDOWN_ORDEM_SEG = int(os.getenv("COOLDOWN_ORDEM_SEG", "300"))
MIN_HOLD_SEG = int(os.getenv("MIN_HOLD_SEG", "300"))
MAX_HOLD_HORAS = float(os.getenv("MAX_HOLD_HORAS", "4"))
MAX_PERDAS_SEGUIDAS = int(os.getenv("MAX_PERDAS_SEGUIDAS", "5"))
PAUSA_PERDAS_HORAS = float(os.getenv("PAUSA_PERDAS_HORAS", "1"))
LIMITE_PERDA_DIA_PCT = float(os.getenv("LIMITE_PERDA_DIA_PCT", "2.0"))
FUNDING_VETO_PCT = float(os.getenv("FUNDING_VETO_PCT", "0.05"))
MIN_AMOSTRA_LADO = int(os.getenv("MIN_AMOSTRA_LADO", "5"))
TEMPO_GUARDA = int(os.getenv("TEMPO_GUARDA", "60"))                # vigia da posição aberta (stop/alvo)
LOG_WAIT_INTERVALO_SEG = int(os.getenv("LOG_WAIT_INTERVALO_SEG", "3600"))
TG_WAIT_A_CADA = int(os.getenv("TG_WAIT_A_CADA", "6"))             # msg de WAIT no Telegram a cada N ciclos
ESTUDO_REFRESH_HORAS = float(os.getenv("ESTUDO_REFRESH_HORAS", "24"))
CACHE_LISTA_SEG = int(os.getenv("CACHE_LISTA_SEG", "300"))
REPETIR_APOS_PERDA_SEG = int(os.getenv("REPETIR_APOS_PERDA_SEG", "1200"))
# Modo aprendizado: se nenhum LLM responder, segue o sinal técnico local em vez de ficar parado
MODO_APRENDIZADO = os.getenv("MODO_APRENDIZADO", "true").lower() == "true"

# Fontes diretas de mercado e referência (sem depender de LLM)
FONTES_REFERENCIA_CRIPTO = [
    "https://www.binance.com/en/academy",
    "https://www.coindesk.com/markets",
    "https://cointelegraph.com/category/markets",
]
ARQUIVO_LICOES_B2 = "memoria/licoes_trades.json"
ARQUIVO_ESTADO_B2 = "memoria/estado_trade.json"

# IAs e Busca Web
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"')
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"')
CLAUDE_MODELO = os.getenv("CLAUDE_MODELO", "claude-haiku-5-5").strip()
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "").strip().strip('"')
USAR_MISTRAL = os.getenv("USAR_MISTRAL", "true").lower() == "true"
TAVILY_KEY = os.getenv("tavily_api_key", os.getenv("TAVILY_API_KEY", "")).strip().strip('"')

# Telegram e execução
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"')
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"')
TG_MAX_IDADE = int(os.getenv("TG_MAX_IDADE", "3600"))
ZEUS_JANELA_SEG = int(os.getenv("ZEUS_JANELA_SEG", "240"))
ZEUS_MODO = os.getenv("ZEUS_MODO", "continuo").strip().lower()

# Ciclos: estudo a cada 30 min (1800s), trade a cada 15 min (900s), relatório a cada 1h (3600s)
TEMPO_ESTUDO = int(os.getenv("TEMPO_ESTUDO", "1800"))
TEMPO_TRADE = int(os.getenv("TEMPO_TRADE", "300"))
TEMPO_RELATORIO_HORARIO = int(os.getenv("TEMPO_RELATORIO_HORARIO", "3600"))

# Timeout curto (conexao, leitura) para pular rapido se uma IA demorar ou travar
TIMEOUT_IA = (4, 8)

# Contadores de desenvoltura na sessão
_inicio_bot_ts = time.time()
_ciclos_trade_rodados = 0
_ciclos_estudo_rodados = 0
_ordens_executadas_sessao = 0
_chamadas_llm_evitadas = 0
_ultimos_aprendizados_sessao = []
_saldo_inicial_sessao = None
_licoes_cache = {"padroes_positivos": [], "alertas_negativos": [], "total_avaliados": 0}
_licoes_carregadas = False
_ultimo_pnl_realizado_avaliado = None  # mantido por compatibilidade
_income_ids_vistos = set()
_pendente_custos = 0.0
_ultimo_log_wait_ts = 0.0
_alavancagem_ok = False
_pausa_manual = False

_estado = {}
_estado_carregado = False
_lock_licoes = threading.Lock()
_lock_estado = threading.Lock()
_lock_ordens = threading.RLock()


def log(msg):
    print(msg, flush=True)


# ================= B2 (API NATIVA) =================
_b2_auth = {}
_b2_bucket_id_cache = None
_cache_b2_arquivos = {}   # nome -> dados (só para arquivos imutáveis)
_cache_b2_lista = {}      # prefixo -> (timestamp, lista)


def b2_authorize():
    global _b2_auth
    if _b2_auth.get("token") and _b2_auth.get("expires", 0) > time.time():
        return _b2_auth
    if not B2_KEY_ID or not B2_APP_KEY:
        return None
    try:
        b64 = base64.b64encode(f"{B2_KEY_ID}:{B2_APP_KEY}".encode()).decode()
        r = requests.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            headers={"Authorization": f"Basic {b64}"},
            timeout=(4, 8),
        )
        if r.status_code == 200:
            d = r.json()
            _b2_auth = {
                "token": d["authorizationToken"],
                "api_url": d["apiUrl"],
                "download_url": d["downloadUrl"],
                "account_id": d.get("accountId", ""),
                "expires": time.time() + 3600,
            }
            log("✅ B2 autorizado com sucesso.")
            return _b2_auth
    except Exception as e:
        log(f"B2 auth erro: {e}")
    return None


def b2_bucket_alvo(auth):
    global _b2_bucket_id_cache
    if not B2_BUCKET:
        return None
    if _b2_bucket_id_cache:
        return _b2_bucket_id_cache
    if re.fullmatch(r"[0-9a-fA-F]{16,32}", B2_BUCKET):
        _b2_bucket_id_cache = B2_BUCKET
        return _b2_bucket_id_cache
    try:
        r = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_list_buckets",
            headers={"Authorization": auth["token"]},
            json={"accountId": auth.get("account_id", ""), "bucketName": B2_BUCKET},
            timeout=(4, 8),
        )
        if r.status_code == 200:
            for b in r.json().get("buckets", []):
                if b.get("bucketName") == B2_BUCKET:
                    _b2_bucket_id_cache = b.get("bucketId")
                    return _b2_bucket_id_cache
    except Exception:
        pass
    _b2_bucket_id_cache = B2_BUCKET
    return _b2_bucket_id_cache


def b2_listar(prefixo, usar_cache=True):
    """Lista arquivos do prefixo, do MAIS ANTIGO para o MAIS NOVO (por data de upload).
    Pagina até 3 páginas e guarda cache curto para não repetir a chamada a cada ciclo."""
    agora = time.time()
    if usar_cache:
        c = _cache_b2_lista.get(prefixo)
        if c and agora - c[0] < CACHE_LISTA_SEG:
            return c[1]
    auth = b2_authorize()
    if not auth:
        return []
    bucket_alvo = b2_bucket_alvo(auth)
    if not bucket_alvo:
        return []
    arquivos = []
    inicio = None
    try:
        for _ in range(3):
            corpo = {"bucketId": bucket_alvo, "prefix": prefixo, "maxFileCount": 1000}
            if inicio:
                corpo["startFileName"] = inicio
            r = requests.post(
                f"{auth['api_url']}/b2api/v2/b2_list_file_names",
                headers={"Authorization": auth["token"]},
                json=corpo,
                timeout=(4, 8),
            )
            if r.status_code == 401:
                _b2_auth.clear()
                break
            if r.status_code != 200:
                break
            d = r.json()
            arquivos.extend(d.get("files", []))
            inicio = d.get("nextFileName")
            if not inicio:
                break
    except Exception:
        pass
    arquivos.sort(key=lambda f: f.get("uploadTimestamp", 0))
    if arquivos:
        _cache_b2_lista[prefixo] = (agora, arquivos)
    return arquivos


def b2_baixar(nome_arquivo, usar_cache=False):
    """usar_cache=True só para arquivos que nunca são sobrescritos (estudos, logs)."""
    if usar_cache and nome_arquivo in _cache_b2_arquivos:
        return _cache_b2_arquivos[nome_arquivo]
    auth = b2_authorize()
    if not auth or not B2_BUCKET:
        return None
    try:
        url = f"{auth['download_url']}/file/{B2_BUCKET}/{urllib.parse.quote(nome_arquivo)}"
        r = requests.get(url, headers={"Authorization": auth["token"]}, timeout=(4, 8))
        if r.status_code == 200:
            dados = r.json()
            if usar_cache:
                if len(_cache_b2_arquivos) > 300:
                    _cache_b2_arquivos.clear()
                _cache_b2_arquivos[nome_arquivo] = dados
            return dados
        if r.status_code == 401:
            _b2_auth.clear()
    except Exception:
        pass
    return None


def b2_salvar(nome_arquivo, dados):
    auth = b2_authorize()
    if not auth:
        return False
    bucket_alvo = b2_bucket_alvo(auth)
    if not bucket_alvo:
        return False
    try:
        r = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_get_upload_url",
            headers={"Authorization": auth["token"]},
            json={"bucketId": bucket_alvo},
            timeout=(4, 8),
        )
        if r.status_code == 401:
            _b2_auth.clear()
            return False
        if r.status_code != 200:
            return False
        up = r.json()
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        sha1 = hashlib.sha1(corpo).hexdigest()
        headers = {
            "Authorization": up["authorizationToken"],
            "X-Bz-File-Name": urllib.parse.quote(nome_arquivo),
            "Content-Type": "application/json",
            "X-Bz-Content-Sha1": sha1,
        }
        r2 = requests.post(up["uploadUrl"], headers=headers, data=corpo, timeout=(4, 10))
        if r2.status_code == 200:
            log(f"✅ Salvo no B2: {nome_arquivo}")
            # invalida só as listagens em cache cujo prefixo cobre este arquivo
            for pref in list(_cache_b2_lista.keys()):
                if nome_arquivo.startswith(pref):
                    _cache_b2_lista.pop(pref, None)
            return True
    except Exception as e:
        log(f"B2 salvar erro: {e}")
    return False


def buscar_estudos_trade_b2(consulta="", limite=5, incluir_licoes=True):
    """Consulta os estudos salvos em estudo_trade/ no B2 (agora do mais novo para o mais antigo,
    com cache de arquivos já baixados). incluir_licoes=False ignora acerto_/erro_ (já vão no prompt à parte)."""
    arquivos = b2_listar("estudo_trade/")
    if not arquivos:
        return []
    palavras = [p for p in re.findall(r"[a-zA-ZÀ-ÿ0-9_]{3,}", consulta.lower())]
    encontrados = []
    for arq in reversed(arquivos[-30:]):
        nome = arq.get("fileName", "")
        if not nome:
            continue
        base = nome.split("/")[-1]
        if not incluir_licoes and (base.startswith("acerto_") or base.startswith("erro_")):
            continue
        dados = b2_baixar(nome, usar_cache=True)
        if not isinstance(dados, dict):
            continue
        texto = f"{dados.get('topico', '')} {dados.get('resumo', '')}".lower()
        pontos = sum(1 for p in palavras if p in texto) if palavras else 1
        if pontos > 0 or not palavras:
            encontrados.append((pontos, int(dados.get("criado_em", 0) or 0), dados))
    encontrados.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item[2] for item in encontrados[:limite]]


# ================= ESTADO PERSISTENTE DO TRADE (B2) =================
def carregar_estado():
    """Estado que sobrevive a reinícios da nuvem: cooldown, pausa, contexto da última entrada."""
    global _estado, _estado_carregado
    with _lock_estado:
        if _estado_carregado:
            return _estado
    dados = b2_baixar(ARQUIVO_ESTADO_B2)
    with _lock_estado:
        if isinstance(dados, dict):
            _estado = dados
        _estado_carregado = True
        return _estado


def salvar_estado(**mudancas):
    carregar_estado()
    with _lock_estado:
        _estado.update(mudancas)
        snap = dict(_estado)
    b2_salvar(ARQUIVO_ESTADO_B2, snap)


# ================= DIÁRIO DE ACERTO E ERRO NOS TRADES (B2) =================
def carregar_licoes_trades():
    global _licoes_cache, _licoes_carregadas
    if _licoes_carregadas:
        return _licoes_cache
    dados = b2_baixar(ARQUIVO_LICOES_B2)
    with _lock_licoes:
        if isinstance(dados, dict):
            _licoes_cache = {
                "padroes_positivos": list(dados.get("padroes_positivos", []))[-25:],
                "alertas_negativos": list(dados.get("alertas_negativos", []))[-25:],
                "total_avaliados": int(dados.get("total_avaliados", 0) or 0),
            }
        _licoes_carregadas = True
    return _licoes_cache


def registrar_licao_trade(resultado_liquido, lado, preco, motivo, custo_taxas=0.0, extras=None):
    """Salva o que funcionou (lucro líquido) ou falhou (prejuízo líquido / taxa alta) no B2."""
    licoes = carregar_licoes_trades()
    with _lock_licoes:
        licoes["total_avaliados"] = int(licoes.get("total_avaliados", 0)) + 1

        registro = {
            "ts": int(time.time()),
            "simbolo": BINANCE_SYMBOL,
            "lado": lado,
            "preco": round(float(preco or 0.0), 2),
            "resultado_liquido_usdt": round(float(resultado_liquido), 4),
            "taxas_usdt": round(float(custo_taxas), 4),
            "motivo": str(motivo or "")[:160],
        }
        if isinstance(extras, dict):
            registro.update(extras)

        if resultado_liquido > 0:
            licoes["padroes_positivos"].append(registro)
            licoes["padroes_positivos"] = licoes["padroes_positivos"][-25:]
            nota = f"Padrão positivo ({lado} @ {registro['preco']}): líquido {resultado_liquido:+.4f} USDT"
            tipo_arq, arq_prefixo = "padrao_positivo", "acerto"
            resumo_extra = "Repetir critérios alinhados a esse contexto."
            topico = f"Acerto trade {BINANCE_SYMBOL} ({lado})"
        else:
            licoes["alertas_negativos"].append(registro)
            licoes["alertas_negativos"] = licoes["alertas_negativos"][-25:]
            nota = (
                f"Como NÃO agir ({lado} @ {registro['preco']}): líquido {resultado_liquido:+.4f} USDT "
                f"(taxas {custo_taxas:+.4f} USDT)"
            )
            tipo_arq, arq_prefixo = "alerta_negativo", "erro"
            resumo_extra = "Evitar entrada apressada ou excesso de giro."
            topico = f"Alerta de erro {BINANCE_SYMBOL} ({lado})"

        _ultimos_aprendizados_sessao.append(nota[:130])
        if len(_ultimos_aprendizados_sessao) > 12:
            del _ultimos_aprendizados_sessao[:-12]
        snap = json.loads(json.dumps(licoes))

    ts = int(time.time())
    b2_salvar(
        f"estudo_trade/{arq_prefixo}_{ts}.json",
        {
            "topico": topico,
            "resumo": f"{nota}. Motivo: {registro['motivo']}. {resumo_extra}",
            "tipo": tipo_arq,
            "criado_em": ts,
        },
    )
    b2_salvar(ARQUIVO_LICOES_B2, snap)
    log(f"[AutoTrade] {nota}")


def estatisticas_lado(lado):
    """Taxa de acerto e resultado médio líquido dos trades anteriores naquele lado (BUY/SELL)."""
    licoes = carregar_licoes_trades()
    pos = [x for x in licoes.get("padroes_positivos", []) if x.get("lado") == lado]
    neg = [x for x in licoes.get("alertas_negativos", []) if x.get("lado") == lado]
    n = len(pos) + len(neg)
    if n == 0:
        return {"n": 0, "winrate": None, "media": 0.0}
    soma = sum(float(x.get("resultado_liquido_usdt", 0) or 0) for x in pos + neg)
    return {"n": n, "winrate": len(pos) / n, "media": soma / n}


def resumo_licoes_para_prompt():
    licoes = carregar_licoes_trades()
    pos = licoes.get("padroes_positivos", [])[-3:]
    neg = licoes.get("alertas_negativos", [])[-3:]
    partes = []
    if pos:
        partes.append(
            "Padrões POSITIVOS (repetir): "
            + "; ".join(f"{p.get('lado')} {p.get('resultado_liquido_usdt'):+.4f} USDT ({p.get('motivo','')})" for p in pos)
        )
    if neg:
        partes.append(
            "Alertas NEGATIVOS (como NÃO agir): "
            + "; ".join(f"{n.get('lado')} {n.get('resultado_liquido_usdt'):+.4f} USDT ({n.get('motivo','')})" for n in neg)
        )
    for lado in ("BUY", "SELL"):
        e = estatisticas_lado(lado)
        if e["n"] >= 3:
            partes.append(f"Histórico {lado}: {e['n']} trades, acerto {e['winrate']*100:.0f}%, média {e['media']:+.4f} USDT")
    return "\n".join(partes)


# ================= DADOS DIRETO DA BINANCE + BUSCA WEB =================
def buscar_dados_mercado_diretos(simbolo=BINANCE_SYMBOL):
    """Busca preço, variação 24h e funding rate direto dos endpoints públicos da Binance."""
    resumo = []
    try:
        r24 = requests.get(
            f"{BINANCE_BASE_URL}/fapi/v1/ticker/24hr",
            params={"symbol": simbolo},
            timeout=(4, 7),
        )
        if r24.status_code == 200:
            d = r24.json()
            resumo.append(
                f"Binance {simbolo}: preço={d.get('lastPrice')} | var24h={d.get('priceChangePercent')}% | "
                f"máx24h={d.get('highPrice')} | mín24h={d.get('lowPrice')} | vol24h={d.get('volume')}"
            )
    except Exception:
        pass

    try:
        rprem = requests.get(
            f"{BINANCE_BASE_URL}/fapi/v1/premiumIndex",
            params={"symbol": simbolo},
            timeout=(4, 7),
        )
        if rprem.status_code == 200:
            p = rprem.json()
            funding = float(p.get("lastFundingRate", 0.0) or 0.0) * 100.0
            resumo.append(f"Mark={p.get('markPrice')} | FundingRate={funding:+.4f}%")
    except Exception:
        pass

    return " | ".join(resumo)


def buscar_web(consulta):
    dados_diretos = buscar_dados_mercado_diretos()
    partes_base = [dados_diretos] if dados_diretos else []

    if TAVILY_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_KEY,
                    "query": consulta,
                    "max_results": 2,
                    "include_answer": True,
                },
                timeout=(4, 8),
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("answer"):
                    partes_base.append(d["answer"])
                for item in d.get("results", [])[:2]:
                    partes_base.append(f"- {item.get('title','')}: {item.get('content','')[:250]}")
                if partes_base:
                    log("[Web] Dados diretos + Tavily coletados.")
                    return "\n".join(partes_base)
            else:
                log(f"Aviso Tavily: {r.status_code}")
        except Exception as e:
            log(f"Erro Tavily: {e}")

    try:
        q = urllib.parse.quote_plus(consulta)
        r = requests.get(
            f"https://html.duckduckgo.com/html/?q={q}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(4, 7),
        )
        if r.status_code == 200:
            titulos = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.S)[:2]
            trechos = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)[:2]
            for t, s in zip(titulos, trechos):
                tl = html.unescape(re.sub(r"<.*?>", "", t)).strip()
                sl = html.unescape(re.sub(r"<.*?>", "", s)).strip()
                if tl:
                    partes_base.append(f"- {tl}: {sl}")
    except Exception:
        pass

    if not partes_base:
        partes_base.append(
            "Fontes de referência para estudo de mercado e gráficos: "
            + ", ".join(FONTES_REFERENCIA_CRIPTO)
        )
    return "\n".join(partes_base)


# ================= IAs =================
_gemini_modelo_ativo = None
_gemini_modelos_invalidos = set()
_gemini_bloqueado_ate = 0.0


def _modelo_gemini_valido_para_texto(nome):
    n = nome.lower()
    bloqueados = ("tts", "image", "audio", "realtime", "live", "embedding", "veo", "imagen")
    if any(b in n for b in bloqueados):
        return False
    return any(tag in n for tag in ("flash", "pro", "neon", "hydrogen", "carbon", "argon"))


def _listar_modelos_gemini():
    global _gemini_modelo_ativo
    if _gemini_modelo_ativo and _gemini_modelo_ativo not in _gemini_modelos_invalidos:
        return [_gemini_modelo_ativo]

    candidatos_padrao = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-4-hydrogen",
        "gemini-4-neon",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
    ]
    if GEMINI_KEY:
        try:
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
                timeout=(3, 6),
            )
            if r.status_code == 200:
                disponiveis = []
                for m in r.json().get("models", []):
                    metodos = m.get("supportedGenerationMethods", [])
                    nome = m.get("name", "").replace("models/", "")
                    if (
                        "generateContent" in metodos
                        and nome not in _gemini_modelos_invalidos
                        and _modelo_gemini_valido_para_texto(nome)
                    ):
                        disponiveis.append(nome)
                priorizados = [c for c in candidatos_padrao if c in disponiveis]
                outros = [d for d in disponiveis if d not in priorizados]
                lista_final = priorizados + outros
                if lista_final:
                    return lista_final
        except Exception:
            pass
    return [c for c in candidatos_padrao if c not in _gemini_modelos_invalidos]


def chamar_gemini(prompt, json_mode=False):
    global _gemini_modelo_ativo, _gemini_bloqueado_ate
    if not GEMINI_KEY:
        return None
    if time.time() < _gemini_bloqueado_ate:
        log("[IA] Gemini em pausa temporária por limite; pulando direto para próxima IA...")
        return None

    for modelo in _listar_modelos_gemini():
        log(f"[IA] Tentando Gemini ({modelo})...")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{modelo}:generateContent?key={GEMINI_KEY}"
        )
        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": "Você é o Zeus. Responda SEMPRE em português do Brasil, de forma clara e direta."
                    }
                ]
            },
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT_IA)
            if r.status_code == 200:
                _gemini_modelo_ativo = modelo
                cands = r.json().get("candidates", [])
                if cands:
                    txt = "".join(
                        p.get("text", "")
                        for p in cands[0].get("content", {}).get("parts", [])
                    ).strip()
                    if txt:
                        return txt
            elif r.status_code == 404:
                log(f"Aviso Gemini ({modelo}): 404; pulando.")
                _gemini_modelos_invalidos.add(modelo)
                if modelo == _gemini_modelo_ativo:
                    _gemini_modelo_ativo = None
            elif r.status_code == 429:
                log(f"Aviso Gemini ({modelo}): limite 429; pausando Gemini por 120s e tentando próxima IA...")
                _gemini_bloqueado_ate = time.time() + 120
                if modelo == _gemini_modelo_ativo:
                    _gemini_modelo_ativo = None
                return None
            else:
                log(f"Aviso Gemini ({modelo}): {r.status_code}; tentando próxima IA...")
                if modelo == _gemini_modelo_ativo:
                    _gemini_modelo_ativo = None
                return None
        except requests.Timeout:
            log(f"Tempo esgotado no Gemini ({modelo}); pulando para próxima IA...")
            if modelo == _gemini_modelo_ativo:
                _gemini_modelo_ativo = None
        except Exception as e:
            log(f"Erro Gemini ({modelo}): {e}; pulando...")
            if modelo == _gemini_modelo_ativo:
                _gemini_modelo_ativo = None
    return None


def chamar_groq(prompt):
    if not GROQ_KEY:
        return None
    modelos_groq = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
    ]
    for modelo in modelos_groq:
        log(f"[IA] Tentando Groq ({modelo})...")
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": modelo,
                    "messages": [
                        {"role": "system", "content": "Você é o Zeus. Responda sempre em português do Brasil."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.4,
                },
                timeout=TIMEOUT_IA,
            )
            if r.status_code == 200:
                txt = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if txt:
                    return txt
            elif r.status_code == 404:
                log(f"Aviso Groq ({modelo}): 404; tentando próximo modelo Groq...")
            else:
                log(f"Aviso Groq ({modelo}): {r.status_code}; pulando para próxima IA...")
                return None
        except requests.Timeout:
            log(f"Tempo esgotado na Groq ({modelo}); pulando para próxima IA...")
            return None
        except Exception as e:
            log(f"Erro Groq ({modelo}): {e}; pulando...")
            return None
    return None


def chamar_openrouter(prompt):
    if not OPENROUTER_KEY:
        return None
    log("[IA] Tentando OpenRouter (openrouter/free)...")
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openrouter/free",
                "messages": [
                    {"role": "system", "content": "Você é o Zeus. Responda sempre em português do Brasil."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            timeout=TIMEOUT_IA,
        )
        if r.status_code == 200:
            txt = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if txt:
                return txt
        else:
            log(f"Aviso OpenRouter: {r.status_code}; pulando para próxima IA...")
    except requests.Timeout:
        log("Tempo esgotado no OpenRouter; pulando para próxima IA...")
    except Exception as e:
        log(f"Erro OpenRouter: {e}; pulando...")
    return None


def chamar_claude(prompt):
    if not ANTHROPIC_KEY:
        return None
    log("[IA] Tentando Claude...")
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODELO,
                "max_tokens": 800,
                "system": "Você é o Zeus. Responda SEMPRE em português do Brasil, de forma clara e objetiva.",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=TIMEOUT_IA,
        )
        if r.status_code == 200:
            blocos = r.json().get("content", [])
            txt = "".join(b.get("text", "") for b in blocos if b.get("type") == "text").strip()
            if txt:
                return txt
        else:
            log(f"Aviso Claude: {r.status_code}; pulando para próxima IA...")
    except requests.Timeout:
        log("Tempo esgotado no Claude; pulando para próxima IA...")
    except Exception as e:
        log(f"Erro Claude: {e}; pulando...")
    return None


def chamar_mistral(prompt):
    if not MISTRAL_KEY or not USAR_MISTRAL:
        return None
    log("[IA] Tentando Mistral...")
    try:
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MISTRAL_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": "Responda sempre em português do Brasil."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            timeout=TIMEOUT_IA,
        )
        if r.status_code == 200:
            return r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        elif r.status_code in (401, 402, 403):
            log(f"Aviso Mistral ({r.status_code}): ignorando neste ciclo.")
        else:
            log(f"Aviso Mistral: {r.status_code}; pulando.")
    except requests.Timeout:
        log("Tempo esgotado no Mistral; pulando.")
    except Exception:
        pass
    return None


def chamar_ia(prompt, contexto_web="", json_mode=False):
    prompt_pt = (
        "Responda SEMPRE em português do Brasil, de forma clara e objetiva, "
        "mesmo que o contexto esteja em inglês.\n\n"
        f"{prompt}"
    )

    for provedor in (chamar_gemini, chamar_groq, chamar_openrouter, chamar_claude, chamar_mistral):
        resp = provedor(prompt_pt, json_mode) if provedor == chamar_gemini else provedor(prompt_pt)
        if resp:
            return resp

    log("[IA] Nenhuma IA respondeu a tempo neste ciclo; usando fallback local/web.")
    if json_mode:
        return json.dumps({
            "acao": "WAIT",
            "motivo": "IAs em limite ou lentas; usando critério seguro na Demo",
            "tamanho": 0,
        })

    if contexto_web:
        trecho = contexto_web[:500].replace("\n", " ")
        return (
            "Registro direto de mercado e fontes sem depender de LLM:\n"
            f"{trecho}"
        )

    return None


def extrair_json(texto):
    """Extrai um objeto JSON mesmo que a IA devolva ```json ... ``` ou texto em volta."""
    if not texto:
        return None
    try:
        return json.loads(texto)
    except Exception:
        pass
    try:
        ini, fim = texto.find("{"), texto.rfind("}")
        if ini != -1 and fim > ini:
            return json.loads(texto[ini:fim + 1])
    except Exception:
        pass
    return None


def resumo_provedores():
    ativos = []
    if B2_KEY_ID and B2_APP_KEY and B2_BUCKET:
        ativos.append("B2")
    if GEMINI_KEY:
        ativos.append("Gemini")
    if GROQ_KEY:
        ativos.append("Groq")
    if OPENROUTER_KEY:
        ativos.append("OpenRouter")
    if ANTHROPIC_KEY:
        ativos.append("Claude")
    if MISTRAL_KEY and USAR_MISTRAL:
        ativos.append("Mistral")
    if TAVILY_KEY:
        ativos.append("Tavily")
    if BINANCE_KEY and BINANCE_SECRET:
        ativos.append("BinanceDemo" if BINANCE_USE_DEMO else "Binance")
    return "Zeus ativos: " + (", ".join(ativos) if ativos else "nenhum configurado")


# ================= MÓDULO DE ESTUDO ACUMULATIVO (estudo_trade/ NO B2) =================
def estudar_e_salvar(tema):
    log(f"[Estudo] Buscando e acumulando conhecimento sobre: {tema}")
    estudos_anteriores = buscar_estudos_trade_b2(tema, limite=5, incluir_licoes=False)

    # Evita gastar LLM/web re-estudando um tema já estudado recentemente
    limite_ts = time.time() - ESTUDO_REFRESH_HORAS * 3600
    if any(
        e.get("topico") == tema and int(e.get("criado_em", 0) or 0) > limite_ts
        for e in estudos_anteriores
    ):
        log(f"[Estudo] Tema já estudado nas últimas {ESTUDO_REFRESH_HORAS:.0f}h; pulando (sem chamar LLM/web).")
        return

    base_anterior = "\n".join(
        f"- [{e.get('topico','')}]: {e.get('resumo','')[:320]}"
        for e in estudos_anteriores[:3]
    )
    licoes_txt = resumo_licoes_para_prompt()

    contexto = buscar_web(f"{tema} alavancagem binance futures")
    prompt = (
        f"Você é um trader especialista em futuros e alavancagem controlada. Crie e acumule "
        f"um guia prático e direto em português do Brasil sobre: {tema}.\n"
        "Foque em leitura de gráficos, estrutura de mercado, pontos de entrada/saída, stop loss, "
        "alavancagem segura, impacto de taxas/funding no lucro líquido e prevenção de perdas excessivas.\n"
    )
    if licoes_txt:
        prompt += f"Lições práticas extraídas dos próprios trades:\n{licoes_txt}\n"
    if base_anterior:
        prompt += (
            "Conhecimento já acumulado em estudo_trade/ (preserve o que funciona, corrija falhas e adicione novas camadas):\n"
            f"{base_anterior}\n"
        )
    if contexto:
        prompt += f"Contexto atual e fontes diretas:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
    if resp:
        slug = re.sub(r"[^a-z0-9_]+", "", tema.lower().replace(" ", "_"))[:25] or "trade"
        ts = int(time.time())
        dados = {
            "topico": tema,
            "resumo": resp,
            "aprimorado_de_estudo_anterior": bool(base_anterior or licoes_txt),
            "criado_em": ts,
        }
        b2_salvar(f"estudo_trade/{slug}_{ts}.json", dados)
        b2_salvar(f"memoria/{slug}_{ts}.json", dados)

        resumo_curto = resp.replace("\n", " ").strip()[:130]
        _ultimos_aprendizados_sessao.append(f"{tema}: {resumo_curto}")
        if len(_ultimos_aprendizados_sessao) > 12:
            del _ultimos_aprendizados_sessao[:-12]


def rotina_estudo():
    global _ciclos_estudo_rodados
    temas = [
        "Alavancagem na Binance Futures e gestão de risco",
        "Leitura de gráficos: suporte, resistência e rompimentos",
        "Tendência, volume e confirmação de entrada em futuros",
        "Stop loss, take profit e proteção contra liquidação",
        "Estratégias de trade com alavancagem controlada",
    ]
    indice = 0
    while True:
        try:
            _ciclos_estudo_rodados += 1
            tema_escolhido = temas[indice % len(temas)]
            indice += 1
            estudar_e_salvar(tema_escolhido)
        except Exception as e:
            log(f"[Estudo] Erro: {e}")
        time.sleep(TEMPO_ESTUDO)


# ================= BINANCE DEMO =================
def binance_request(method, endpoint, params=None, assinado=True):
    if not BINANCE_KEY or not BINANCE_SECRET:
        return None
    if params is None:
        params = {}
    if assinado:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{BINANCE_BASE_URL}{endpoint}?{query}&signature={sig}"
    else:
        qs = urllib.parse.urlencode(params)
        url = f"{BINANCE_BASE_URL}{endpoint}" + (f"?{qs}" if qs else "")

    try:
        if method == "GET":
            r = requests.get(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=(4, 8))
        else:
            r = requests.post(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=(4, 8))
        dados = r.json()
        if r.status_code != 200:
            log(f"[Binance] {endpoint} retornou {r.status_code}: {dados}")
        return dados if r.status_code in (200, 400) else None
    except Exception as e:
        log(f"[Binance] Erro em {endpoint}: {e}")
        return None


def consultar_saldo():
    global _saldo_inicial_sessao
    conta = binance_request("GET", "/fapi/v2/account")
    if not conta or "totalWalletBalance" not in conta:
        return None
    saldo_atual = float(conta.get("totalWalletBalance", 0))
    if _saldo_inicial_sessao is None:
        _saldo_inicial_sessao = saldo_atual
    return {
        "saldo": saldo_atual,
        "pnl": float(conta.get("totalUnrealizedProfit", 0)),
    }


def consultar_resultados_binance():
    """Consulta saldo, PnL aberto e ganhos/perdas realizados recentes na Binance Demo."""
    conta = binance_request("GET", "/fapi/v2/account")
    posicoes = binance_request("GET", "/fapi/v2/positionRisk")
    rendimentos = binance_request(
        "GET",
        "/fapi/v1/income",
        {"limit": 1000, "startTime": int((time.time() - 86400) * 1000)},
    )

    if not conta or "totalWalletBalance" not in conta:
        return None

    saldo = float(conta.get("totalWalletBalance", 0.0))
    pnl_aberto = float(conta.get("totalUnrealizedProfit", 0.0))

    ganhos_realizados = 0.0
    perdas_realizadas = 0.0
    taxas_e_funding = 0.0
    itens = []

    if isinstance(rendimentos, list):
        itens = rendimentos
        for item in rendimentos:
            tipo = str(item.get("incomeType", ""))
            valor = float(item.get("income", 0.0) or 0.0)
            if tipo == "REALIZED_PNL":
                if valor > 0:
                    ganhos_realizados += valor
                elif valor < 0:
                    perdas_realizadas += valor
            elif tipo in ("COMMISSION", "FUNDING_FEE"):
                taxas_e_funding += valor

    posicoes_abertas = []
    if isinstance(posicoes, list):
        for p in posicoes:
            qtd = float(p.get("positionAmt", 0.0) or 0.0)
            if qtd != 0:
                pnl_p = float(p.get("unRealizedProfit", 0.0) or 0.0)
                posicoes_abertas.append(
                    f"{p.get('symbol')} {qtd} (PnL: {pnl_p:+.4f} USDT)"
                )

    return {
        "saldo": saldo,
        "pnl_aberto": pnl_aberto,
        "ganhos_realizados": ganhos_realizados,
        "perdas_realizadas": perdas_realizadas,
        "taxas_e_funding": taxas_e_funding,
        "posicoes_abertas": posicoes_abertas,
        "itens": itens,
    }


def _avaliar_mudanca_resultado_realizado(res_binance, preco, ultimo_lado, ultimo_motivo):
    """Detecta trades FECHADOS (REALIZED_PNL novo), soma as taxas/funding do trade e salva a lição no B2.
    Cada lançamento da Binance é contado uma única vez (por tranId), sem depender da janela móvel de 24h."""
    global _pendente_custos
    if not res_binance:
        return
    corte_ms = int(_inicio_bot_ts * 1000)
    novos = []
    for it in res_binance.get("itens") or []:
        sym = str(it.get("symbol") or "")
        if sym and sym != BINANCE_SYMBOL:
            continue
        try:
            ts_ms = int(it.get("time", 0) or 0)
        except Exception:
            ts_ms = 0
        if ts_ms < corte_ms:
            continue
        chave = f"{it.get('tranId')}|{it.get('incomeType')}|{ts_ms}|{it.get('income')}"
        if chave in _income_ids_vistos:
            continue
        _income_ids_vistos.add(chave)
        novos.append(it)

    realizado = 0.0
    custos = 0.0
    for it in novos:
        tipo = str(it.get("incomeType", ""))
        valor = float(it.get("income", 0.0) or 0.0)
        if tipo == "REALIZED_PNL":
            realizado += valor
        elif tipo in ("COMMISSION", "FUNDING_FEE"):
            custos += valor

    if realizado == 0.0:
        # só taxa de abertura/funding: acumula e espera o fechamento do trade
        _pendente_custos += custos
        return

    liquido = realizado + custos + _pendente_custos
    taxas = custos + _pendente_custos
    _pendente_custos = 0.0

    estado = carregar_estado()
    lado = estado.get("entrada_lado") or ultimo_lado or "MERCADO"
    entrada_ts = int(estado.get("entrada_ts", 0) or 0)
    extras = {
        "score": estado.get("entrada_score"),
        "sl_pct": estado.get("sl_pct"),
        "tp_pct": estado.get("tp_pct"),
        "duracao_min": round((time.time() - entrada_ts) / 60.0, 1) if entrada_ts else None,
        "saida": estado.get("ultimo_motivo_saida"),
    }
    registrar_licao_trade(
        resultado_liquido=liquido,
        lado=lado,
        preco=estado.get("entrada_preco") or preco,
        motivo=estado.get("entrada_motivo") or ultimo_motivo or "Fechamento avaliado na Demo",
        custo_taxas=taxas,
        extras=extras,
    )

    # Disjuntor: perdas líquidas seguidas pausam novas entradas
    seguidas = int(estado.get("perdas_seguidas", 0) or 0)
    if liquido < 0:
        seguidas += 1
    else:
        seguidas = 0
    mudancas = {"perdas_seguidas": seguidas}
    if seguidas >= MAX_PERDAS_SEGUIDAS:
        mudancas["pausa_ate"] = time.time() + PAUSA_PERDAS_HORAS * 3600
        mudancas["perdas_seguidas"] = 0
        msg = f"⏸️ Zeus pausou novas entradas por {PAUSA_PERDAS_HORAS:.0f}h após {seguidas} perdas líquidas seguidas."
        log(f"[Risco] {msg}")
        enviar_telegram(msg)
    salvar_estado(**mudancas)


# ================= SINAL TÉCNICO LOCAL (SEM LLM) =================
def _ema(valores, n):
    k = 2.0 / (n + 1.0)
    e = valores[0]
    for v in valores[1:]:
        e = v * k + e * (1.0 - k)
    return e


def _rsi(fechamentos, n=14):
    if len(fechamentos) < n + 1:
        return None
    ganhos = perdas = 0.0
    for i in range(1, n + 1):
        d = fechamentos[i] - fechamentos[i - 1]
        ganhos += max(d, 0.0)
        perdas += max(-d, 0.0)
    mg, mp = ganhos / n, perdas / n
    for i in range(n + 1, len(fechamentos)):
        d = fechamentos[i] - fechamentos[i - 1]
        mg = (mg * (n - 1) + max(d, 0.0)) / n
        mp = (mp * (n - 1) + max(-d, 0.0)) / n
    if mp == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + mg / mp)


def _atr_pct(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][1], candles[i][2], candles[i - 1][3]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n or candles[-1][3] <= 0:
        return None
    return (sum(trs[-n:]) / n) / candles[-1][3] * 100.0


def buscar_klines(intervalo, limite=100, simbolo=None):
    """Candles FECHADOS (descarta o último, ainda em formação): lista de (open, high, low, close, volume)."""
    try:
        r = requests.get(
            f"{BINANCE_BASE_URL}/fapi/v1/klines",
            params={"symbol": simbolo or BINANCE_SYMBOL, "interval": intervalo, "limit": limite},
            timeout=(4, 7),
        )
        if r.status_code == 200:
            dados = r.json()
            return [
                (float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                for k in dados[:-1]
            ]
    except Exception:
        pass
    return None


def _funding_atual_pct():
    try:
        r = requests.get(
            f"{BINANCE_BASE_URL}/fapi/v1/premiumIndex",
            params={"symbol": BINANCE_SYMBOL},
            timeout=(4, 7),
        )
        if r.status_code == 200:
            return float(r.json().get("lastFundingRate", 0.0) or 0.0) * 100.0
    except Exception:
        pass
    return None


def calcular_sinal_tecnico():
    """Score de -4 a +4 (positivo = compra). Usa só dados públicos da Binance: zero custo de LLM.
    Componentes: EMA9/21 em 15m, EMA9/21 em 1h, RSI 15m (zona de tendência) e momentum de 2h."""
    k15 = buscar_klines("15m", 100)
    k1h = buscar_klines("1h", 100)
    if not k15 or not k1h or len(k15) < 30 or len(k1h) < 30:
        return None
    c15 = [c[3] for c in k15]
    c1h = [c[3] for c in k1h]
    ema9_15, ema21_15 = _ema(c15, 9), _ema(c15, 21)
    ema9_1h, ema21_1h = _ema(c1h, 9), _ema(c1h, 21)
    rsi = _rsi(c15, 14)
    atr = _atr_pct(k15, 14)
    if rsi is None or atr is None:
        return None
    mom_2h = (c15[-1] / c15[-9] - 1.0) * 100.0 if len(c15) >= 9 and c15[-9] > 0 else 0.0

    s1 = 1 if ema9_15 > ema21_15 else -1
    s2 = 1 if ema9_1h > ema21_1h else -1
    s3 = 1 if 52 <= rsi <= 68 else (-1 if 32 <= rsi <= 48 else 0)
    s4 = 1 if mom_2h > 0.3 else (-1 if mom_2h < -0.3 else 0)
    score = s1 + s2 + s3 + s4

    return {
        "score": score,
        "rsi": round(rsi, 1),
        "atr_pct": round(atr, 3),
        "mom_2h_pct": round(mom_2h, 3),
        "tendencia_1h": "alta" if s2 > 0 else "baixa",
        "funding_pct": _funding_atual_pct(),
    }


def _vetos_sinal(lado, sinal):
    """Condições que impedem a entrada mesmo com score alto (custo, esticado, mercado morto)."""
    if sinal["atr_pct"] < MIN_ATR_PCT:
        return f"volatilidade baixa demais (ATR {sinal['atr_pct']}% < {MIN_ATR_PCT}%): não cobre as taxas"
    if lado == "BUY" and sinal["rsi"] >= 75:
        return f"RSI esticado ({sinal['rsi']}): evitando comprar topo"
    if lado == "SELL" and sinal["rsi"] <= 25:
        return f"RSI esticado ({sinal['rsi']}): evitando vender fundo"
    f = sinal.get("funding_pct")
    if f is not None:
        if lado == "BUY" and f > FUNDING_VETO_PCT:
            return f"funding {f:+.4f}% caro para comprado"
        if lado == "SELL" and f < -FUNDING_VETO_PCT:
            return f"funding {f:+.4f}% caro para vendido"
    return None


def _planejar_stop_alvo(atr_pct):
    """Stop adaptativo à volatilidade e alvo = stop x RR. Só vale entrar se o alvo cobre N x as taxas ida+volta."""
    sl = min(STOP_MAX_PCT, max(STOP_MIN_PCT, ATR_STOP_MULT * atr_pct))
    tp = sl * ALVO_RR
    custo_ida_volta = 2.0 * (TAXA_TAKER_PCT + SLIPPAGE_PCT)
    ok = tp >= FATOR_TAXA_MIN * custo_ida_volta
    return sl, tp, custo_ida_volta, ok


# ================= POSIÇÃO / ORDENS =================
def _calcular_quantidade_demo(saldo_usdt, preco, stop_pct=None):
    """Quantidade com alavancagem controlada (<=2x). Limita por margem %, por risco no stop e pelo teto de 2x do saldo.
    Retorna 0.0 se não der para respeitar o notional mínimo sem estourar o limite."""
    if preco <= 0 or saldo_usdt <= 0:
        return 0.0
    alav = max(1.0, min(BINANCE_MAX_LEVERAGE, 2.0))
    notional = saldo_usdt * BINANCE_MARGEM_PCT * alav
    if stop_pct and stop_pct > 0:
        notional = min(notional, (saldo_usdt * RISCO_POR_TRADE_PCT / 100.0) / (stop_pct / 100.0))
    teto = saldo_usdt * alav * 0.9
    notional = min(notional, teto)
    notional = max(notional, BINANCE_MIN_NOTIONAL)
    if notional > teto:
        return 0.0
    fator = 10 ** BINANCE_QTY_DECIMALS
    qtd = math.floor(notional / preco * fator) / fator
    return qtd if qtd > 0 else 0.0


def _posicao_atual_simbolo():
    posicoes = binance_request("GET", "/fapi/v2/positionRisk", {"symbol": BINANCE_SYMBOL})
    if isinstance(posicoes, list):
        for p in posicoes:
            if p.get("symbol") == BINANCE_SYMBOL:
                return float(p.get("positionAmt", 0.0) or 0.0)
    return 0.0


def _posicao_detalhe():
    posicoes = binance_request("GET", "/fapi/v2/positionRisk", {"symbol": BINANCE_SYMBOL})
    if isinstance(posicoes, list):
        for p in posicoes:
            if p.get("symbol") == BINANCE_SYMBOL:
                qtd = float(p.get("positionAmt", 0.0) or 0.0)
                if qtd != 0:
                    return {
                        "qtd": qtd,
                        "entrada": float(p.get("entryPrice", 0.0) or 0.0),
                        "mark": float(p.get("markPrice", 0.0) or 0.0),
                    }
    return None


def garantir_alavancagem():
    """Define a alavancagem (<=2x) uma vez por sessão, em vez de a cada ordem."""
    global _alavancagem_ok
    if _alavancagem_ok:
        return
    alav_int = int(max(1, min(BINANCE_MAX_LEVERAGE, 2)))
    r = binance_request("POST", "/fapi/v1/leverage", {"symbol": BINANCE_SYMBOL, "leverage": alav_int})
    if isinstance(r, dict) and r.get("leverage"):
        _alavancagem_ok = True


def enviar_ordem(lado, qtd, reduce_only=False):
    """Envia ordem a mercado. Só conta como executada se a Binance devolver orderId."""
    global _ordens_executadas_sessao
    with _lock_ordens:
        garantir_alavancagem()
        params = {
            "symbol": BINANCE_SYMBOL,
            "side": lado,
            "type": "MARKET",
            "quantity": f"{qtd:.{BINANCE_QTY_DECIMALS}f}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        ordem = binance_request("POST", "/fapi/v1/order", params)
        ok = isinstance(ordem, dict) and bool(ordem.get("orderId"))
        if ok:
            _ordens_executadas_sessao += 1
        return ordem, ok


def fechar_posicao(pos, motivo):
    lado_fecha = "SELL" if pos["qtd"] > 0 else "BUY"
    ordem, ok = enviar_ordem(lado_fecha, abs(pos["qtd"]), reduce_only=True)
    if ok:
        salvar_estado(ultima_ordem_ts=time.time(), ultimo_motivo_saida=motivo, pico_pct=0.0)
        log(f"[Trade] Posição fechada ({motivo}).")
        b2_salvar(f"trades/log_{int(time.time())}.json", {
            "preco": pos["mark"], "decisao": {"acao": "FECHAR", "motivo": motivo},
            "ordem": ordem, "ts": int(time.time()),
        })
    else:
        log(f"[Trade] Falha ao fechar posição ({motivo}): {ordem}")
    return ok


def gerir_posicao(sinal=None):
    """Gestão de posição 100% local (sem LLM): stop adaptativo, alvo, trailing, tempo máximo e reversão forte.
    Retorna decisão (dict) se fechou, None caso contrário."""
    with _lock_ordens:
        pos = _posicao_detalhe()
        estado = carregar_estado()
        if not pos or pos["entrada"] <= 0:
            if estado.get("pico_pct"):
                salvar_estado(pico_pct=0.0)
            return None

        direcao = 1 if pos["qtd"] > 0 else -1
        pct = (pos["mark"] - pos["entrada"]) / pos["entrada"] * 100.0 * direcao
        sl = float(estado.get("sl_pct") or STOP_MIN_PCT * 2)
        tp = float(estado.get("tp_pct") or sl * ALVO_RR)
        entrada_ts = float(estado.get("entrada_ts") or 0)
        if not entrada_ts:
            entrada_ts = time.time()
            salvar_estado(entrada_ts=entrada_ts)
        idade = time.time() - entrada_ts
        pico = max(float(estado.get("pico_pct") or 0.0), pct)
        if pico > float(estado.get("pico_pct") or 0.0) + 0.05:
            _estado["pico_pct"] = pico  # atualiza em memória; persiste no próximo salvar_estado

        motivo = None
        if pct <= -sl:
            motivo = f"Stop loss ({pct:+.2f}% <= -{sl:.2f}%)"
        elif pct >= tp:
            motivo = f"Take profit ({pct:+.2f}% >= {tp:.2f}%)"
        elif pico >= tp * 0.5 and pct <= pico * 0.4:
            motivo = f"Trailing: devolveu ganho (pico {pico:+.2f}% -> {pct:+.2f}%)"
        elif idade > MAX_HOLD_HORAS * 3600:
            motivo = f"Tempo máximo na posição ({idade/3600:.1f}h)"
        elif (
            sinal and idade >= MIN_HOLD_SEG
            and sinal["score"] * direcao <= -SCORE_FORTE
        ):
            motivo = f"Reversão forte do sinal técnico (score {sinal['score']:+d})"

        if not motivo:
            return None
        if fechar_posicao(pos, motivo):
            enviar_telegram(f"🔒 Zeus fechou posição {BINANCE_SYMBOL} ({pos['qtd']}): {motivo}")
            return {"acao": "FECHAR", "motivo": motivo, "tamanho": abs(pos["qtd"])}
        return None


def rotina_guarda_posicao():
    """Vigia leve (1 chamada/min) para o stop/alvo não esperar o ciclo de 15 min."""
    while True:
        time.sleep(TEMPO_GUARDA)
        try:
            if BINANCE_KEY and BINANCE_SECRET:
                gerir_posicao(sinal=None)
        except Exception as e:
            log(f"[Guarda] Erro: {e}")


# ================= DECISÃO DE TRADE =================
def _bloqueio_entrada(saldo_usdt, res_detalhado):
    """Bloqueios objetivos antes de qualquer análise cara (LLM/web). Retorna texto ou None."""
    estado = carregar_estado()
    agora = time.time()
    if _pausa_manual:
        return "Pausa manual ativa (comando 'retomar trade' para voltar)."
    pausa_ate = float(estado.get("pausa_ate") or 0)
    if pausa_ate > agora:
        return f"Pausa por perdas seguidas por mais {(pausa_ate - agora)/3600:.1f}h."
    ultima = float(estado.get("ultima_ordem_ts") or 0)
    if agora - ultima < COOLDOWN_ORDEM_SEG:
        return f"Cooldown pós-ordem: faltam {int(COOLDOWN_ORDEM_SEG - (agora - ultima))//60} min."
    if res_detalhado and saldo_usdt > 0:
        liq24 = (
            res_detalhado["ganhos_realizados"]
            + res_detalhado["perdas_realizadas"]
            + res_detalhado["taxas_e_funding"]
        )
        if liq24 <= -saldo_usdt * LIMITE_PERDA_DIA_PCT / 100.0:
            return f"Limite de perda diária atingido ({liq24:+.2f} USDT em 24h)."
    return None


def _registrar_wait(log_acao, motivo, preco):
    """Salva log de WAIT no B2 no máximo 1x por LOG_WAIT_INTERVALO_SEG (evita ~96 arquivos/dia)."""
    global _ultimo_log_wait_ts
    if time.time() - _ultimo_log_wait_ts >= LOG_WAIT_INTERVALO_SEG:
        b2_salvar(f"trades/log_{int(time.time())}.json", log_acao)
        _ultimo_log_wait_ts = time.time()
    log(f"[Trade] Aguardando (WAIT) em {preco}: {motivo}")


def analisar_e_operar():
    global _chamadas_llm_evitadas

    if not BINANCE_KEY or not BINANCE_SECRET:
        log("[Trade] Binance sem chave/segredo configurados.")
        return {"acao": "WAIT", "motivo": "Chaves da Binance não configuradas", "tamanho": 0}

    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": BINANCE_SYMBOL}, assinado=False)
    if not ticker or "price" not in ticker:
        return {"acao": "WAIT", "motivo": "Falha ao obter preço da Binance", "tamanho": 0}
    preco = float(ticker["price"])

    saldo_info = consultar_saldo()
    saldo_usdt = saldo_info["saldo"] if saldo_info else 100.0
    pos_atual = _posicao_atual_simbolo()

    res_detalhado = consultar_resultados_binance()
    estado = carregar_estado()
    _avaliar_mudanca_resultado_realizado(
        res_detalhado,
        preco,
        estado.get("entrada_lado") or ("BUY" if pos_atual >= 0 else "SELL"),
        estado.get("entrada_motivo") or "Ciclo de avaliação da Demo",
    )

    sinal = calcular_sinal_tecnico()
    log_acao = {"preco": preco, "sinal": sinal, "ts": int(time.time())}

    # 1) Posição aberta: gestão local, sem LLM e sem abrir ordem nova na mesma direção (evita empilhar posição)
    if pos_atual != 0 and not TESTE_AUTO_DEMO:
        fechou = gerir_posicao(sinal)
        if fechou:
            return fechou
        d = {"acao": "WAIT", "motivo": f"Mantendo posição aberta ({pos_atual}); stop/alvo sob gestão local.", "tamanho": 0}
        _registrar_wait({**log_acao, "decisao": d}, d["motivo"], preco)
        return d

    decisao = {"acao": "WAIT", "motivo": "Sem vantagem clara", "tamanho": 0}
    acao = "WAIT"
    stop_pct = tp_pct = None

    if not TESTE_AUTO_DEMO:
        # 2) Bloqueios objetivos (cooldown, pausa, limite diário)
        bloqueio = _bloqueio_entrada(saldo_usdt, res_detalhado)
        if bloqueio:
            decisao["motivo"] = bloqueio
        elif not sinal:
            decisao["motivo"] = "Sem dados técnicos (klines) neste ciclo."
        else:
            forca = abs(sinal["score"])
            lado_local = "BUY" if sinal["score"] > 0 else "SELL"
            stats = estatisticas_lado(lado_local) if forca else {"n": 0, "winrate": None, "media": 0.0}
            lado_ruim = (
                stats["n"] >= MIN_AMOSTRA_LADO and stats["winrate"] < 0.35 and stats["media"] < 0
            )
            exigido = SCORE_FORTE if lado_ruim else SCORE_MIN
            licoes = carregar_licoes_trades()
            ultimo_neg = next(
                (x for x in reversed(licoes.get("alertas_negativos", [])) if x.get("lado") == lado_local),
                None,
            )
            veto = _vetos_sinal(lado_local, sinal) if forca else None
            sl_pct, tp_alvo, custo_rt, alvo_ok = _planejar_stop_alvo(sinal["atr_pct"])

            if forca < exigido:
                decisao["motivo"] = (
                    f"Sinal fraco (score {sinal['score']:+d}, exigido {exigido}"
                    f"{' - lado com histórico ruim' if lado_ruim else ''}); sem chamar LLM."
                )
                _chamadas_llm_evitadas += 1
            elif veto:
                decisao["motivo"] = f"Veto: {veto}."
                _chamadas_llm_evitadas += 1
            elif not alvo_ok:
                decisao["motivo"] = (
                    f"Alvo {tp_alvo:.2f}% não cobre {FATOR_TAXA_MIN:.0f}x as taxas ({custo_rt:.2f}% ida+volta)."
                )
                _chamadas_llm_evitadas += 1
            elif ultimo_neg and (time.time() - int(ultimo_neg.get("ts", 0))) < REPETIR_APOS_PERDA_SEG:
                decisao["motivo"] = (
                    f"Evitando repetir {lado_local} após prejuízo recente "
                    f"({ultimo_neg.get('resultado_liquido_usdt'):+.4f} USDT)."
                )
                _chamadas_llm_evitadas += 1
            else:
                # Sinal local forte e histórico favorável dispensam o LLM; senão o LLM só confirma/veta
                dispensa_llm = (
                    forca >= SCORE_FORTE and stats["n"] >= MIN_AMOSTRA_LADO
                    and stats["winrate"] >= 0.6 and stats["media"] > 0
                )
                if dispensa_llm:
                    _chamadas_llm_evitadas += 1
                    acao = lado_local
                    decisao = {
                        "acao": acao,
                        "motivo": f"Sinal técnico forte ({sinal['score']:+d}) + histórico {lado_local} favorável (sem LLM)",
                        "tamanho": 0,
                    }
                else:
                    estudos = buscar_estudos_trade_b2(
                        f"trade alavancagem {BINANCE_SYMBOL}", limite=3, incluir_licoes=False
                    )
                    estrategia = "Estratégia acumulada: seguir tendência, usar alavancagem até 2x e evitar giro excessivo."
                    if estudos:
                        estrategia = "\n".join(
                            f"- {e.get('topico','')}: {e.get('resumo','')[:280]}" for e in estudos
                        )
                    licoes_txt = resumo_licoes_para_prompt()
                    dados_mercado = buscar_dados_mercado_diretos(BINANCE_SYMBOL)
                    prompt = f"""Você é o Zeus Trader operando com alavancagem controlada na Binance Futures Demo.
Objetivo: lucro LÍQUIDO consistente (descontando taxas, funding e perdas), sem giro desnecessário.
O sinal técnico local sugere {lado_local} (score {sinal['score']:+d} de 4): RSI15m={sinal['rsi']}, ATR15m={sinal['atr_pct']}%, momentum 2h={sinal['mom_2h_pct']}%, tendência 1h={sinal['tendencia_1h']}, funding={sinal.get('funding_pct')}%.
Plano: stop {sl_pct:.2f}%, alvo {tp_alvo:.2f}%, custo de taxas ida+volta ~{custo_rt:.2f}%.
Confirme {lado_local} SOMENTE se o contexto abaixo reforça a entrada; caso contrário responda WAIT. Não proponha o lado oposto.
Dados diretos de mercado: {dados_mercado}
Conhecimento acumulado em estudo_trade/: {estrategia}
Histórico de acertos e erros próprios: {licoes_txt or 'Ainda sem histórico suficiente; seja cauteloso.'}
Preço atual do {BINANCE_SYMBOL}: {preco} | Saldo Demo: {saldo_usdt:.2f} USDT | Alavancagem máxima: {int(BINANCE_MAX_LEVERAGE)}x
Responda APENAS um JSON válido com: {{"acao": "{lado_local}"|"WAIT", "motivo": "texto curto em português"}}"""
                    resp = chamar_ia(prompt, dados_mercado, json_mode=True)
                    dec_llm = extrair_json(resp) or {}
                    acao_llm = str(dec_llm.get("acao", "WAIT")).upper().strip()
                    llm_fora = (not dec_llm) or str(dec_llm.get("motivo", "")).startswith("IAs em limite")
                    if llm_fora and MODO_APRENDIZADO:
                        acao = lado_local
                        decisao = {
                            "acao": acao,
                            "motivo": f"[{sinal['score']:+d}] LLM indisponível; seguindo sinal técnico (modo aprendizado)",
                            "tamanho": 0,
                        }
                    elif acao_llm == lado_local:
                        acao = lado_local
                        decisao = {
                            "acao": acao,
                            "motivo": f"[{sinal['score']:+d}] {str(dec_llm.get('motivo', ''))[:140]}",
                            "tamanho": 0,
                        }
                    else:
                        decisao["motivo"] = (
                            f"LLM não confirmou {lado_local}: {str(dec_llm.get('motivo', 'indisponível'))[:120]}"
                        )
                if acao in ("BUY", "SELL"):
                    stop_pct, tp_pct = sl_pct, tp_alvo
    else:
        # Modo de teste explícito (TESTE_AUTO_DEMO=true): alterna posição como antes, sem filtros
        if pos_atual > 0:
            acao = "SELL"
            decisao = {"acao": acao, "motivo": "TESTE_AUTO_DEMO ativo: encerrando posição comprada.", "tamanho": 0}
        elif pos_atual < 0:
            acao = "BUY"
            decisao = {"acao": acao, "motivo": "TESTE_AUTO_DEMO ativo: encerrando posição vendida.", "tamanho": 0}
        else:
            acao = "BUY" if (_ciclos_trade_rodados % 2 == 1) else "SELL"
            decisao = {
                "acao": acao,
                "motivo": f"TESTE_AUTO_DEMO ativo ({int(BINANCE_MAX_LEVERAGE)}x): teste controlado.",
                "tamanho": 0,
            }

    log_acao["decisao"] = decisao

    if acao in ("BUY", "SELL"):
        fechando = pos_atual != 0 and ((pos_atual > 0 and acao == "SELL") or (pos_atual < 0 and acao == "BUY"))
        if fechando:
            qtd_final = abs(pos_atual)
        else:
            qtd_final = _calcular_quantidade_demo(saldo_usdt, preco, stop_pct)
        if qtd_final <= 0:
            decisao.update({"acao": "WAIT", "motivo": "Saldo insuficiente para o notional mínimo dentro do limite de 2x.", "tamanho": 0})
            _registrar_wait(log_acao, decisao["motivo"], preco)
            return decisao

        ordem, ok = enviar_ordem(acao, qtd_final, reduce_only=fechando)
        log_acao["ordem"] = ordem
        b2_salvar(f"trades/log_{int(time.time())}.json", log_acao)
        if ok:
            decisao["tamanho"] = qtd_final
            novo = {"ultima_ordem_ts": time.time()}
            if not fechando:
                novo.update({
                    "entrada_ts": time.time(),
                    "entrada_lado": acao,
                    "entrada_preco": preco,
                    "entrada_motivo": decisao.get("motivo", "")[:160],
                    "entrada_score": sinal["score"] if sinal else None,
                    "sl_pct": stop_pct,
                    "tp_pct": tp_pct,
                    "pico_pct": 0.0,
                    "ultimo_motivo_saida": None,
                })
            else:
                novo["ultimo_motivo_saida"] = "TESTE_AUTO_DEMO"
            salvar_estado(**novo)
            alav_int = int(max(1, min(BINANCE_MAX_LEVERAGE, 2)))
            log(f"[Trade] Ordem {acao} ({qtd_final} {BINANCE_SYMBOL} @ {alav_int}x) enviada na Demo em {preco}.")
        else:
            decisao.update({"acao": "WAIT", "motivo": f"Ordem rejeitada pela Binance: {str(ordem)[:120]}", "tamanho": 0})
            log(f"[Trade] Ordem {acao} rejeitada: {ordem}")
    else:
        decisao["tamanho"] = 0
        _registrar_wait(log_acao, decisao.get("motivo", ""), preco)

    return decisao


def enviar_telegram(msg, chat_id=None):
    destino = str(chat_id or TG_CHAT_ID).strip()
    if not TG_TOKEN or not destino:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": destino, "text": msg[:4000]},
            timeout=(4, 8),
        )
    except Exception:
        pass


def montar_relatorio_de_hora_em_hora():
    """Monta o resumo horário com lucro/prejuízo, saldo, desenvoltura e aprendizados acumulados."""
    horas_ativas = max(0.1, (time.time() - _inicio_bot_ts) / 3600.0)
    res = consultar_resultados_binance()

    if res:
        resultado_realizado_liquido = (
            res["ganhos_realizados"]
            + res["perdas_realizadas"]
            + res["taxas_e_funding"]
        )
        resultado_total_com_aberto = resultado_realizado_liquido + res["pnl_aberto"]
        variacao_sessao = (
            f"{res['saldo'] - _saldo_inicial_sessao:+.4f} USDT"
            if _saldo_inicial_sessao is not None
            else "sem base inicial"
        )
        bloco_binance = (
            f"💰 Saldo Demo: {res['saldo']:.2f} USDT (variação sessão: {variacao_sessao})\n"
            f"🟢 Lucro realizado (24h): +{res['ganhos_realizados']:.4f} USDT\n"
            f"🔴 Prejuízo realizado (24h): {res['perdas_realizadas']:.4f} USDT\n"
            f"💸 Taxas/Funding (24h): {res['taxas_e_funding']:.4f} USDT\n"
            f"📊 Resultado líquido realizado (24h): {resultado_realizado_liquido:+.4f} USDT\n"
            f"📈 PnL aberto agora: {res['pnl_aberto']:+.4f} USDT\n"
            f"🧮 Resultado total (realizado + aberto): {resultado_total_com_aberto:+.4f} USDT\n"
            "📌 Posições abertas: "
            + (
                ", ".join(res["posicoes_abertas"])
                if res["posicoes_abertas"]
                else "nenhuma"
            )
        )
    else:
        bloco_binance = "⚠️ Não consegui ler os dados da Binance Demo neste ciclo."

    if _ultimos_aprendizados_sessao:
        bloco_aprendizado = "\n".join(f"• {item}" for item in _ultimos_aprendizados_sessao[-4:])
    else:
        estudos = buscar_estudos_trade_b2("", limite=4, incluir_licoes=False)
        topicos = [str(e.get("topico", "")).strip() for e in estudos if e.get("topico")]
        bloco_aprendizado = (
            "\n".join(f"• {t}" for t in topicos[:4])
            if topicos
            else "• Aguardando próximo ciclo de estudo em estudo_trade/ no B2."
        )

    desenvoltura = (
        f"⏱️ Online há {horas_ativas:.1f}h | Ciclos de trade: {_ciclos_trade_rodados} "
        f"| Ordens enviadas: {_ordens_executadas_sessao} | Ciclos de estudo: {_ciclos_estudo_rodados} "
        f"| Chamadas LLM evitadas: {_chamadas_llm_evitadas}"
    )

    estado = carregar_estado()
    pausa_ate = float(estado.get("pausa_ate") or 0)
    linhas_risco = []
    for lado in ("BUY", "SELL"):
        e = estatisticas_lado(lado)
        if e["n"]:
            linhas_risco.append(f"{lado}: {e['n']} trades, acerto {e['winrate']*100:.0f}%, média {e['media']:+.4f} USDT")
    if _pausa_manual:
        linhas_risco.append("⏸️ Pausa manual ativa")
    elif pausa_ate > time.time():
        linhas_risco.append(f"⏸️ Pausa por perdas seguidas por mais {(pausa_ate - time.time())/3600:.1f}h")
    bloco_risco = ("\n🎯 Desempenho próprio:\n" + "\n".join(f"• {l}" for l in linhas_risco)) if linhas_risco else ""

    return (
        "⏰ RELATÓRIO HORÁRIO DO ZEUS (BINANCE DEMO)\n\n"
        f"{bloco_binance}\n\n"
        f"⚡ Desenvoltura:\n{desenvoltura}\n"
        f"{bloco_risco}\n\n"
        f"📚 O que ele aprendeu e acumulou:\n{bloco_aprendizado}"
    )


def rotina_relatorio_horario():
    """Envia o relatório no Telegram a cada 1 hora (3600s) sem interromper os outros loops."""
    while True:
        time.sleep(TEMPO_RELATORIO_HORARIO)
        try:
            if TG_TOKEN and TG_CHAT_ID:
                msg = montar_relatorio_de_hora_em_hora()
                enviar_telegram(msg, chat_id=TG_CHAT_ID)
                log("✅ Relatório horário enviado ao Telegram.")
        except Exception as e:
            log(f"[Relatório Horário] Erro: {e}")


def rotina_trade():
    global _ciclos_trade_rodados
    while True:
        try:
            _ciclos_trade_rodados += 1
            log("[Trade] Iniciando ciclo de análise e operação...")
            decisao = analisar_e_operar()
            saldo = consultar_saldo()

            # WAIT repetido não precisa de mensagem a cada ciclo; ações sempre são notificadas
            notificar = decisao.get("acao", "WAIT") != "WAIT" or (_ciclos_trade_rodados % max(1, TG_WAIT_A_CADA) == 0)
            if saldo and notificar:
                msg = (
                    "🤖 Relatório Zeus Trader\n"
                    f"💰 Saldo: {saldo['saldo']:.2f} USDT\n"
                    f"📈 PnL: {saldo['pnl']:.2f} USDT\n"
                    f"🎯 Ação: {decisao.get('acao', 'WAIT')} ({decisao.get('tamanho', 0)})\n"
                    f"🧠 Motivo: {decisao.get('motivo', 'Sem detalhes')}"
                )
                enviar_telegram(msg)
        except Exception as e:
            log(f"[Trade] Erro na rotina: {e}")
        time.sleep(TEMPO_TRADE)


# ================= TELEGRAM LOOP (MAIN) =================
def gerar_resposta(texto):
    estudos = buscar_estudos_trade_b2(texto, limite=2, incluir_licoes=False)
    if estudos and any(k in texto.lower() for k in ("estudo", "trade", "alavanc", "grafico", "gráfico", "suporte", "risco")):
        top = estudos[0]
        return f"📘 De estudo_trade/ ({top.get('topico','')}):\n\n{top.get('resumo','')}"

    arquivos = b2_listar("memoria/")
    for arq in reversed(arquivos[-5:]):
        if arq["fileName"] in (ARQUIVO_LICOES_B2, ARQUIVO_ESTADO_B2):
            continue
        dados = b2_baixar(arq["fileName"], usar_cache=True)
        if isinstance(dados, dict) and texto.lower() in dados.get("topico", "").lower():
            return f"📚 Da minha memória:\n\n{dados.get('resumo', '')}"

    contexto = buscar_web(texto)
    prompt = (
        "VOCÊ É O ZEUS. Responda SEMPRE em português do Brasil, de forma direta e amigável.\n"
        f"Usuário: {texto}\n"
    )
    if estudos:
        resumo_est = "\n".join(f"- {e.get('topico','')}: {e.get('resumo','')[:220]}" for e in estudos)
        prompt += f"\nEstudos salvos em estudo_trade/:\n{resumo_est}\n"
    if contexto:
        prompt += f"Contexto da web e fontes diretas:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
    if resp and any(k in texto.lower() for k in ("trade", "alavanc", "grafico", "gráfico", "suporte", "risco", "mercado", "btc", "futuros")):
        ts = int(time.time())
        b2_salvar(
            f"estudo_trade/consulta_{ts}.json",
            {"topico": texto[:120], "resumo": resp[:1500], "criado_em": ts},
        )
    return resp or "Poxa, meus servidores estão ocupados. Tente novamente em instantes!"


def tratar_update(att):
    global _pausa_manual
    msg = att.get("message") or att.get("edited_message") or {}
    texto = (msg.get("text") or "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    data_msg = int(msg.get("date", 0) or 0)

    if not texto or not chat_id:
        return
    if TG_CHAT_ID and chat_id != TG_CHAT_ID:
        return
    if ZEUS_MODO == "janela" and data_msg and (time.time() - data_msg) > TG_MAX_IDADE:
        return

    texto_lower = texto.lower()
    if texto_lower == "status trade":
        s = consultar_saldo()
        resp = (
            f"💰 Saldo: {s['saldo']:.2f} | 📈 PnL: {s['pnl']:.2f}"
            if s
            else "❌ Erro ao acessar Binance Demo."
        )
    elif texto_lower in ("relatorio", "relatório"):
        resp = montar_relatorio_de_hora_em_hora()
    elif texto_lower in ("estudos trade", "ver estudos", "estudo trade"):
        estudos = buscar_estudos_trade_b2("", limite=5, incluir_licoes=False)
        if estudos:
            linhas = [f"• {e.get('topico','')} — {e.get('resumo','')[:140]}..." for e in estudos]
            resp = "📘 Estudos salvos em estudo_trade/:\n\n" + "\n\n".join(linhas)
        else:
            resp = "Ainda não há arquivos em estudo_trade/ no B2. Aguarde o próximo ciclo de estudo."
    elif texto_lower in ("licoes", "lições", "licoes trade", "lições trade"):
        resp = "📒 Diário de trades:\n\n" + (resumo_licoes_para_prompt() or "Ainda sem trades fechados avaliados.")
    elif texto_lower == "pausar trade":
        _pausa_manual = True
        resp = "⏸️ Novas entradas pausadas. Posições abertas continuam sob stop/alvo. Envie 'retomar trade' para voltar."
    elif texto_lower == "retomar trade":
        _pausa_manual = False
        salvar_estado(pausa_ate=0, perdas_seguidas=0)
        resp = "▶️ Entradas retomadas."
    else:
        resp = gerar_resposta(texto)

    enviar_telegram(resp, chat_id=chat_id)


def main():
    if not TG_TOKEN:
        log("Erro: TELEGRAM_BOT_TOKEN não configurado.")
        return

    log(resumo_provedores())
    log(f"[Config] Binance {'DEMO' if BINANCE_USE_DEMO else 'REAL'} -> {BINANCE_BASE_URL} | TESTE_AUTO_DEMO={TESTE_AUTO_DEMO} | ciclo de trade {TEMPO_TRADE}s")
    custo_rt = 2.0 * (TAXA_TAKER_PCT + SLIPPAGE_PCT)
    log(
        f"[Config] Alavancagem máx {BINANCE_MAX_LEVERAGE}x | custo ida+volta ~{custo_rt:.2f}% | "
        f"stop {STOP_MIN_PCT}-{STOP_MAX_PCT}% | RR {ALVO_RR} | score mín {SCORE_MIN} | cooldown {COOLDOWN_ORDEM_SEG}s"
    )

    if ZEUS_MODO == "janela":
        base = f"https://api.telegram.org/bot{TG_TOKEN}"
        try:
            requests.get(f"{base}/deleteWebhook", timeout=(4, 8))
        except Exception:
            pass

        fim = time.time() + max(10, ZEUS_JANELA_SEG)
        offset = None
        while time.time() < fim:
            restante = max(1, min(25, int(fim - time.time())))
            params = {"timeout": restante}
            if offset:
                params["offset"] = offset
            try:
                r = requests.get(f"{base}/getUpdates", params=params, timeout=restante + 10)
                if r.status_code == 409:
                    log("ERRO 409: outro processo já está lendo este bot. Encerrando janela.")
                    break
                if r.status_code != 200:
                    time.sleep(2)
                    continue
                for att in r.json().get("result", []):
                    offset = att["update_id"] + 1
                    tratar_update(att)
            except Exception as e:
                log(f"Erro na janela Telegram: {e}")
                break

        if offset:
            try:
                requests.get(f"{base}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=(4, 8))
            except Exception:
                pass
        return

    carregar_estado()
    threading.Thread(target=rotina_estudo, daemon=True).start()
    time.sleep(3)
    threading.Thread(target=rotina_trade, daemon=True).start()
    time.sleep(2)
    threading.Thread(target=rotina_relatorio_horario, daemon=True).start()
    threading.Thread(target=rotina_guarda_posicao, daemon=True).start()

    offset = None
    while True:
        try:
            params = {"timeout": 20}
            if offset:
                params["offset"] = offset

            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                params=params,
                timeout=25,
            )
            if r.status_code == 409:
                log("Aviso 409: outro leitor ativo no mesmo token; aguardando 10s...")
                time.sleep(10)
                continue
            if r.status_code != 200:
                time.sleep(3)
                continue

            for att in r.json().get("result", []):
                offset = att["update_id"] + 1
                tratar_update(att)

        except Exception as e:
            log(f"Erro no loop do Telegram: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
