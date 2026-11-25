import os
import json
import time
import hmac
import hashlib
import base64
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
BINANCE_MARGEM_PCT = float(os.getenv("BINANCE_MARGEM_PCT", "0.05"))  # 5% do saldo por ordem de teste
TESTE_AUTO_DEMO = os.getenv("TESTE_AUTO_DEMO", "true").lower() == "true"

# IAs e Busca Web
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"')
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip().strip('"')
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"')
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
TEMPO_TRADE = int(os.getenv("TEMPO_TRADE", "900"))
TEMPO_RELATORIO_HORARIO = int(os.getenv("TEMPO_RELATORIO_HORARIO", "3600"))

# Timeout curto (conexao, leitura) para pular rapido se uma IA demorar ou travar
TIMEOUT_IA = (4, 8)

# Contadores de desenvoltura na sessão
_inicio_bot_ts = time.time()
_ciclos_trade_rodados = 0
_ciclos_estudo_rodados = 0
_ordens_executadas_sessao = 0
_ultimos_aprendizados_sessao = []
_saldo_inicial_sessao = None


def log(msg):
    print(msg, flush=True)


# ================= B2 (API NATIVA) =================
_b2_auth = {}
_b2_bucket_id_cache = None


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


def b2_listar(prefixo):
    auth = b2_authorize()
    if not auth:
        return []
    bucket_alvo = b2_bucket_alvo(auth)
    if not bucket_alvo:
        return []
    try:
        r = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_list_file_names",
            headers={"Authorization": auth["token"]},
            json={"bucketId": bucket_alvo, "prefix": prefixo, "maxFileCount": 50},
            timeout=(4, 8),
        )
        if r.status_code == 200:
            return r.json().get("files", [])
    except Exception:
        pass
    return []


def b2_baixar(nome_arquivo):
    auth = b2_authorize()
    if not auth or not B2_BUCKET:
        return None
    try:
        url = f"{auth['download_url']}/file/{B2_BUCKET}/{urllib.parse.quote(nome_arquivo)}"
        r = requests.get(url, headers={"Authorization": auth["token"]}, timeout=(4, 8))
        if r.status_code == 200:
            return r.json()
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
            return True
    except Exception as e:
        log(f"B2 salvar erro: {e}")
    return False


def buscar_estudos_trade_b2(consulta="", limite=5):
    """Consulta e acumula os estudos salvos em estudo_trade/ no B2."""
    arquivos = b2_listar("estudo_trade/")
    if not arquivos:
        return []
    palavras = [p for p in re.findall(r"[a-zA-ZÀ-ÿ0-9_]{3,}", consulta.lower())]
    encontrados = []
    for arq in reversed(arquivos[-20:]):
        nome = arq.get("fileName", "")
        if not nome:
            continue
        dados = b2_baixar(nome)
        if not isinstance(dados, dict):
            continue
        texto = f"{dados.get('topico', '')} {dados.get('resumo', '')}".lower()
        pontos = sum(1 for p in palavras if p in texto) if palavras else 1
        if pontos > 0 or not palavras:
            encontrados.append((pontos, dados))
    encontrados.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in encontrados[:limite]]


# ================= BUSCA WEB (TAVILY + FALLBACK) =================
def buscar_web(consulta):
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
                partes = []
                if d.get("answer"):
                    partes.append(d["answer"])
                for item in d.get("results", [])[:2]:
                    partes.append(f"- {item.get('title','')}: {item.get('content','')[:250]}")
                if partes:
                    log("[Web] Tavily encontrou resultados.")
                    return "\n".join(partes)
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
            titulos = re.findall(r'class="result__a"[^>]>(.?)</a>', r.text, re.S)[:2]
            trechos = re.findall(
                r'class="result__snippet"[^>]>(.?)</a>', r.text, re.S
            )[:2]
            linhas = []
            for t, s in zip(titulos, trechos):
                tl = html.unescape(re.sub(r"<.*?>", "", t)).strip()
                sl = html.unescape(re.sub(r"<.*?>", "", s)).strip()
                if tl:
                    linhas.append(f"- {tl}: {sl}")
            if linhas:
                return "\n".join(linhas)
    except Exception:
        pass
    return ""


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
                "model": "claude-3-5-haiku-latest",
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
        trecho = contexto_web[:450].replace("\n", " ")
        return (
            "As IAs estão com limite ou demora agora, mas aqui está o resumo "
            f"encontrado na busca web: {trecho}"
        )

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
    estudos_anteriores = buscar_estudos_trade_b2(tema, limite=3)
    base_anterior = "\n".join(
        f"- [{e.get('topico','')}]: {e.get('resumo','')[:320]}"
        for e in estudos_anteriores
    )

    contexto = buscar_web(f"{tema} alavancagem binance futures")
    prompt = (
        f"Você é um trader especialista em futuros e alavancagem controlada. Crie e acumule "
        f"um guia prático e direto em português do Brasil sobre: {tema}.\n"
        "Foque em leitura de gráficos, estrutura de mercado, pontos de entrada/saída, stop loss, "
        "alavancagem segura e prevenção de perdas excessivas.\n"
    )
    if base_anterior:
        prompt += (
            "Conhecimento já acumulado em estudo_trade/ (preserve o que funciona, corrija falhas e adicione novas camadas):\n"
            f"{base_anterior}\n"
        )
    if contexto:
        prompt += f"Contexto atual:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
    if resp:
        slug = re.sub(r"[^a-z0-9_]+", "", tema.lower().replace(" ", "_"))[:25] or "trade"
        ts = int(time.time())
        dados = {
            "topico": tema,
            "resumo": resp,
            "aprimorado_de_estudo_anterior": bool(base_anterior),
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
        {"limit": 100, "startTime": int((time.time() - 86400) * 1000)},
    )

    if not conta or "totalWalletBalance" not in conta:
        return None

    saldo = float(conta.get("totalWalletBalance", 0.0))
    pnl_aberto = float(conta.get("totalUnrealizedProfit", 0.0))

    ganhos_realizados = 0.0
    perdas_realizadas = 0.0
    taxas_e_funding = 0.0

    if isinstance(rendimentos, list):
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
    }


def _calcular_quantidade_demo(saldo_usdt, preco):
    """Calcula quantidade em BTC com alavancagem controlada e margem segura na Demo."""
    if preco <= 0 or saldo_usdt <= 0:
        return 0.001
    alav = max(1.0, min(BINANCE_MAX_LEVERAGE, 2.0))
    notional_alvo = max(5.0, saldo_usdt * BINANCE_MARGEM_PCT * alav)
    qtd = round(notional_alvo / preco, 3)
    return max(0.001, qtd)


def _posicao_atual_simbolo():
    posicoes = binance_request("GET", "/fapi/v2/positionRisk", {"symbol": BINANCE_SYMBOL})
    if isinstance(posicoes, list):
        for p in posicoes:
            if p.get("symbol") == BINANCE_SYMBOL:
                return float(p.get("positionAmt", 0.0) or 0.0)
    return 0.0


def analisar_e_operar():
    global _ordens_executadas_sessao

    if not BINANCE_KEY or not BINANCE_SECRET:
        log("[Trade] Binance sem chave/segredo configurados.")
        return {"acao": "WAIT", "motivo": "Chaves da Binance não configuradas", "tamanho": 0}

    estudos = buscar_estudos_trade_b2(f"trade alavancagem {BINANCE_SYMBOL}", limite=3)
    estrategia = "Estratégia acumulada: seguir tendência, usar alavancagem até 2x e stop curto."
    if estudos:
        estrategia = "\n".join(
            f"- {e.get('topico','')}: {e.get('resumo','')[:300]}" for e in estudos
        )
    else:
        arquivos = b2_listar("memoria/")
        if arquivos:
            recente = b2_baixar(arquivos[-1]["fileName"])
            if recente:
                estrategia = recente.get("resumo", estrategia)[:500]

    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": BINANCE_SYMBOL}, assinado=False)
    if not ticker or "price" not in ticker:
        return {"acao": "WAIT", "motivo": "Falha ao obter preço da Binance"}
    preco = float(ticker["price"])

    saldo_info = consultar_saldo()
    saldo_usdt = saldo_info["saldo"] if saldo_info else 100.0
    pos_atual = _posicao_atual_simbolo()

    prompt = f"""Você é o Zeus Trader operando com alavancagem controlada na Binance Futures Demo.
Decida se deve ABRIR (BUY/SELL) ou AGUARDAR (WAIT).
Conhecimento acumulado em estudo_trade/: {estrategia}
Preço atual do {BINANCE_SYMBOL}: {preco} | Saldo Demo: {saldo_usdt:.2f} USDT | Posição atual: {pos_atual} | Alavancagem máxima: {int(BINANCE_MAX_LEVERAGE)}x
Responda APENAS um JSON válido com: {{"acao": "BUY"|"SELL"|"WAIT", "motivo": "texto curto em português", "tamanho": 0.001}}"""

    resp = chamar_ia(prompt, json_mode=True)
    try:
        decisao = json.loads(resp)
    except Exception:
        decisao = {"acao": "WAIT", "motivo": "IA indisponível no momento", "tamanho": 0}

    acao = str(decisao.get("acao", "WAIT")).upper().strip()

    # Modo de teste prático na Demo: se a IA ficar em WAIT, alterna teste controlado para validar ordens e PnL
    if acao == "WAIT" and TESTE_AUTO_DEMO:
        if pos_atual > 0:
            acao = "SELL"
            decisao["acao"] = "SELL"
            decisao["motivo"] = "Teste prático na Demo: realizando/fechando posição aberta para medir resultado."
        elif pos_atual < 0:
            acao = "BUY"
            decisao["acao"] = "BUY"
            decisao["motivo"] = "Teste prático na Demo: realizando/fechando posição vendida para medir resultado."
        else:
            acao = "BUY" if (_ciclos_trade_rodados % 2 == 1) else "SELL"
            decisao["acao"] = acao
            decisao["motivo"] = f"Teste prático na Demo ({int(BINANCE_MAX_LEVERAGE)}x): abrindo operação para avaliar ganho/perda."

    qtd_solicitada = float(decisao.get("tamanho") or 0.0)
    qtd_final = qtd_solicitada if qtd_solicitada >= 0.001 else _calcular_quantidade_demo(saldo_usdt, preco)
    decisao["tamanho"] = qtd_final

    log_acao = {"preco": preco, "decisao": decisao, "ts": int(time.time())}
    if acao in ("BUY", "SELL"):
        alav_int = int(max(1, min(BINANCE_MAX_LEVERAGE, 2)))
        binance_request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": BINANCE_SYMBOL, "leverage": alav_int},
        )
        ordem = binance_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": BINANCE_SYMBOL,
                "side": acao,
                "type": "MARKET",
                "quantity": qtd_final,
            },
        )
        log_acao["ordem"] = ordem
        _ordens_executadas_sessao += 1
        b2_salvar(f"trades/log_{int(time.time())}.json", log_acao)
        log(f"[Trade] Ordem {acao} ({qtd_final} {BINANCE_SYMBOL} @ {alav_int}x) enviada na Demo em {preco}.")
    else:
        b2_salvar(f"trades/log_{int(time.time())}.json", log_acao)

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
        estudos = buscar_estudos_trade_b2("", limite=4)
        topicos = [str(e.get("topico", "")).strip() for e in estudos if e.get("topico")]
        bloco_aprendizado = (
            "\n".join(f"• {t}" for t in topicos[:4])
            if topicos
            else "• Aguardando próximo ciclo de estudo em estudo_trade/ no B2."
        )

    desenvoltura = (
        f"⏱️ Online há {horas_ativas:.1f}h | Ciclos de trade: {_ciclos_trade_rodados} "
        f"| Ordens enviadas: {_ordens_executadas_sessao} | Ciclos de estudo: {_ciclos_estudo_rodados}"
    )

    return (
        "⏰ RELATÓRIO HORÁRIO DO ZEUS (BINANCE DEMO)\n\n"
        f"{bloco_binance}\n\n"
        f"⚡ Desenvoltura:\n{desenvoltura}\n\n"
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

            if saldo:
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
    estudos = buscar_estudos_trade_b2(texto, limite=2)
    if estudos and any(k in texto.lower() for k in ("estudo", "trade", "alavanc", "grafico", "gráfico", "suporte", "risco")):
        top = estudos[0]
        return f"📘 De estudo_trade/ ({top.get('topico','')}):\n\n{top.get('resumo','')}"

    arquivos = b2_listar("memoria/")
    for arq in reversed(arquivos[-5:]):
        dados = b2_baixar(arq["fileName"])
        if dados and texto.lower() in dados.get("topico", "").lower():
            return f"📚 Da minha memória:\n\n{dados['resumo']}"

    contexto = buscar_web(texto)
    prompt = (
        "VOCÊ É O ZEUS. Responda SEMPRE em português do Brasil, de forma direta e amigável.\n"
        f"Usuário: {texto}\n"
    )
    if estudos:
        resumo_est = "\n".join(f"- {e.get('topico','')}: {e.get('resumo','')[:220]}" for e in estudos)
        prompt += f"\nEstudos salvos em estudo_trade/:\n{resumo_est}\n"
    if contexto:
        prompt += f"Contexto da web:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
    if resp and any(k in texto.lower() for k in ("trade", "alavanc", "grafico", "gráfico", "suporte", "risco", "mercado", "btc", "futuros")):
        ts = int(time.time())
        b2_salvar(
            f"estudo_trade/consulta_{ts}.json",
            {"topico": texto[:120], "resumo": resp[:1500], "criado_em": ts},
        )
    return resp or "Poxa, meus servidores estão ocupados. Tente novamente em instantes!"


def tratar_update(att):
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
        estudos = buscar_estudos_trade_b2("", limite=5)
        if estudos:
            linhas = [f"• {e.get('topico','')} — {e.get('resumo','')[:140]}..." for e in estudos]
            resp = "📘 Estudos salvos em estudo_trade/:\n\n" + "\n\n".join(linhas)
        else:
            resp = "Ainda não há arquivos em estudo_trade/ no B2. Aguarde o próximo ciclo de estudo."
    else:
        resp = gerar_resposta(texto)

    enviar_telegram(resp, chat_id=chat_id)


def main():
    if not TG_TOKEN:
        log("Erro: TELEGRAM_BOT_TOKEN não configurado.")
        return

    log(resumo_provedores())

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

    threading.Thread(target=rotina_estudo, daemon=True).start()
    time.sleep(3)
    threading.Thread(target=rotina_trade, daemon=True).start()
    time.sleep(2)
    threading.Thread(target=rotina_relatorio_horario, daemon=True).start()

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
