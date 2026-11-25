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
BINANCE_KEY = os.getenv("BINANCE_API_KEY", "").strip().strip('"')
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", "").strip().strip('"')
BINANCE_USE_DEMO = os.getenv("BINANCE_USE_DEMO", "true").lower() == "true"
BINANCE_MAX_LEVERAGE = float(os.getenv("BINANCE_MAX_LEVERAGE", "2"))
BINANCE_BASE_URL = "https://demo-fapi.binance.com" if BINANCE_USE_DEMO else "https://fapi.binance.com"
BINANCE_SYMBOL = "BTCUSDT"

# IA
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
GROK_KEY = os.getenv("GROK_API_KEY", "").strip().strip('"')
TAVILY_KEY = os.getenv("tavily_api_key", os.getenv("TAVILY_API_KEY", "")).strip().strip('"')
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "").strip().strip('"')
USAR_MISTRAL = os.getenv("USAR_MISTRAL", "false").lower() == "true"

# Telegram e execução
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"')
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"')
TG_MAX_IDADE = int(os.getenv("TG_MAX_IDADE", "3600"))
ZEUS_JANELA_SEG = int(os.getenv("ZEUS_JANELA_SEG", "240"))
ZEUS_MODO = os.getenv("ZEUS_MODO", "continuo").strip().lower()

TEMPO_ESTUDO = 300
TEMPO_TRADE = 300

# ================= B2 (API NATIVA) =================
_b2_auth = {}


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
            timeout=15,
        )
        if r.status_code == 200:
            d = r.json()
            _b2_auth = {
                "token": d["authorizationToken"],
                "api_url": d["apiUrl"],
                "download_url": d["downloadUrl"],
                "expires": time.time() + 3600,
            }
            print("✅ B2 autorizado com sucesso.")
            return _b2_auth
    except Exception as e:
        print(f"B2 auth erro: {e}")
    return None


def b2_listar(prefixo):
    auth = b2_authorize()
    if not auth:
        return []
    try:
        r = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_list_file_names",
            headers={"Authorization": auth["token"]},
            json={"bucketId": B2_BUCKET, "prefix": prefixo, "maxFileCount": 50},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("files", [])
    except Exception:
        pass
    return []


def b2_baixar(nome_arquivo):
    auth = b2_authorize()
    if not auth:
        return None
    try:
        url = f"{auth['download_url']}/file/{B2_BUCKET}/{urllib.parse.quote(nome_arquivo)}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def b2_salvar(nome_arquivo, dados):
    auth = b2_authorize()
    if not auth:
        return False
    try:
        r = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_get_upload_url",
            headers={"Authorization": auth["token"]},
            json={"bucketId": B2_BUCKET},
            timeout=15,
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
        r2 = requests.post(up["uploadUrl"], headers=headers, data=corpo, timeout=20)
        if r2.status_code == 200:
            print(f"✅ Salvo no B2: {nome_arquivo}")
            return True
    except Exception as e:
        print(f"B2 salvar erro: {e}")
    return False


# ================= BUSCA WEB (TAVILY + FALLBACK) =================
def buscar_web(consulta):
    if TAVILY_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_KEY,
                    "query": consulta,
                    "max_results": 3,
                    "include_answer": True,
                },
                timeout=15,
            )
            if r.status_code == 200:
                d = r.json()
                partes = []
                if d.get("answer"):
                    partes.append(d["answer"])
                for item in d.get("results", [])[:2]:
                    partes.append(f"- {item.get('title','')}: {item.get('content','')[:300]}")
                if partes:
                    print("[Web] Tavily encontrou resultados.")
                    return "\n".join(partes)
            else:
                print(f"Aviso Tavily: {r.status_code}")
        except Exception as e:
            print(f"Erro Tavily: {e}")

    try:
        q = urllib.parse.quote_plus(consulta)
        r = requests.get(
            f"https://html.duckduckgo.com/html/?q={q}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
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


def _listar_modelos_gemini():
    global _gemini_modelo_ativo
    if _gemini_modelo_ativo:
        return [_gemini_modelo_ativo]

    candidatos = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
    ]
    if GEMINI_KEY:
        try:
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
                timeout=12,
            )
            if r.status_code == 200:
                disponiveis = []
                for m in r.json().get("models", []):
                    metodos = m.get("supportedGenerationMethods", [])
                    nome = m.get("name", "").replace("models/", "")
                    if "generateContent" in metodos and "flash" in nome:
                        disponiveis.append(nome)
                if disponiveis:
                    return disponiveis + [c for c in candidatos if c not in disponiveis]
        except Exception:
            pass
    return candidatos


def chamar_mistral(prompt):
    if not MISTRAL_KEY or not USAR_MISTRAL:
        return None
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
            timeout=25,
        )
        if r.status_code == 200:
            return r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        elif r.status_code == 402:
            print("❌ Mistral: Sem créditos (Erro 402). Ignorando...")
        else:
            print(f"Aviso Mistral: {r.status_code}")
    except Exception:
        pass
    return None


def chamar_gemini(prompt, json_mode=False):
    global _gemini_modelo_ativo
    if not GEMINI_KEY:
        return None

    for modelo in _listar_modelos_gemini():
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
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1000},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(url, json=payload, timeout=25)
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
            elif r.status_code in (429, 403):
                print(f"Aviso Gemini ({modelo}): limite/bloqueio {r.status_code}.")
                if modelo == _gemini_modelo_ativo:
                    _gemini_modelo_ativo = None
            else:
                print(f"Aviso Gemini ({modelo}): {r.status_code}")
                if modelo == _gemini_modelo_ativo:
                    _gemini_modelo_ativo = None
        except Exception:
            if modelo == _gemini_modelo_ativo:
                _gemini_modelo_ativo = None
    return None


def chamar_grok(prompt):
    if not GROK_KEY:
        return None
    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROK_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-2-latest",
                "messages": [
                    {"role": "system", "content": "Responda sempre em português do Brasil."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=25,
        )
        if r.status_code == 200:
            return r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        pass
    return None


def chamar_ia(prompt, contexto_web="", json_mode=False):
    prompt_pt = (
        "Responda SEMPRE em português do Brasil, de forma clara e objetiva, "
        "mesmo que o contexto esteja em inglês.\n\n"
        f"{prompt}"
    )

    resp = chamar_gemini(prompt_pt, json_mode)
    if resp:
        return resp

    resp = chamar_mistral(prompt_pt)
    if resp:
        return resp

    resp = chamar_grok(prompt_pt)
    if resp:
        return resp

    if json_mode:
        return json.dumps({
            "acao": "WAIT",
            "motivo": "IA em limite temporário; aguardando próxima janela em português",
            "tamanho": 0,
        })

    if contexto_web:
        trecho = contexto_web[:450].replace("\n", " ")
        return (
            "As IAs estão com limite/bloqueio temporário agora, mas aqui está o resumo "
            f"encontrado na busca web: {trecho}"
        )

    return None


def resumo_provedores():
    ativos = []
    if B2_KEY_ID and B2_APP_KEY and B2_BUCKET:
        ativos.append("B2")
    if GEMINI_KEY:
        ativos.append("Gemini")
    if GROK_KEY:
        ativos.append("Grok")
    if TAVILY_KEY:
        ativos.append("Tavily")
    if BINANCE_KEY and BINANCE_SECRET:
        ativos.append("BinanceDemo" if BINANCE_USE_DEMO else "Binance")
    return "Zeus ativos: " + (", ".join(ativos) if ativos else "nenhum configurado")


# ================= MÓDULO DE ESTUDO =================
def estudar_e_salvar(tema):
    print(f"[Estudo] Buscando conhecimento sobre: {tema}")
    contexto = buscar_web(tema)
    prompt = (
        f"Você é um trader especialista. Crie um guia prático e direto em português do Brasil sobre: {tema}.\n"
        "Foque em regras de entrada, saída, gestão de risco e alavancagem segura.\n"
    )
    if contexto:
        prompt += f"Contexto atual:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
    if resp:
        dados = {"topico": tema, "resumo": resp, "criado_em": int(time.time())}
        nome = f"memoria/{tema.lower().replace(' ', '')[:30]}{int(time.time())}.json"
        b2_salvar(nome, dados)


def rotina_estudo():
    temas = [
        "Alavancagem e gerenciamento de risco em cripto",
        "Análise técnica: suporte, resistência e tendências",
        "Estratégias de trade para iniciantes na Binance Futures",
        "Psicologia do trading e controle emocional",
    ]
    while True:
        try:
            tema_escolhido = temas[int(time.time()) % len(temas)]
            estudar_e_salvar(tema_escolhido)
        except Exception as e:
            print(f"[Estudo] Erro: {e}")
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
            r = requests.get(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=15)
        else:
            r = requests.post(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=15)
        return r.json() if r.status_code in (200, 400) else None
    except Exception:
        return None


def consultar_saldo():
    conta = binance_request("GET", "/fapi/v2/account")
    if not conta or "totalWalletBalance" not in conta:
        return None
    return {
        "saldo": float(conta.get("totalWalletBalance", 0)),
        "pnl": float(conta.get("totalUnrealizedProfit", 0)),
    }


def analisar_e_operar():
    arquivos = b2_listar("memoria/")
    estrategia = "Estratégia padrão: seguir tendência e usar stop curto."
    if arquivos:
        recente = b2_baixar(arquivos[-1]["fileName"])
        if recente:
            estrategia = recente.get("resumo", estrategia)[:500]

    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": BINANCE_SYMBOL}, assinado=False)
    if not ticker or "price" not in ticker:
        return {"acao": "WAIT", "motivo": "Falha ao obter preço da Binance"}
    preco = float(ticker["price"])

    prompt = f"""Você é o Zeus Trader. Decida se deve ABRIR (BUY/SELL) ou AGUARDAR (WAIT).
Estratégia da memória: {estrategia}
Preço atual do {BINANCE_SYMBOL}: {preco}
Responda APENAS um JSON válido com: {{"acao": "BUY"|"SELL"|"WAIT", "motivo": "texto curto em português", "tamanho": 0.001}}"""

    resp = chamar_ia(prompt, json_mode=True)
    try:
        decisao = json.loads(resp)
    except Exception:
        decisao = {"acao": "WAIT", "motivo": "IA indisponível no momento", "tamanho": 0}

    log_acao = {"preco": preco, "decisao": decisao, "ts": int(time.time())}
    if decisao.get("acao") in ["BUY", "SELL"]:
        binance_request("POST", "/fapi/v1/leverage", {"symbol": BINANCE_SYMBOL, "leverage": int(BINANCE_MAX_LEVERAGE)})
        ordem = binance_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": BINANCE_SYMBOL,
                "side": decisao["acao"],
                "type": "MARKET",
                "quantity": decisao.get("tamanho", 0.001),
            },
        )
        log_acao["ordem"] = ordem
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
            timeout=15,
        )
    except Exception:
        pass


def rotina_trade():
    while True:
        try:
            print("[Trade] Iniciando ciclo de análise e operação...")
            decisao = analisar_e_operar()
            saldo = consultar_saldo()

            if saldo:
                msg = (
                    "🤖 Relatório Zeus Trader\n"
                    f"💰 Saldo: {saldo['saldo']:.2f} USDT\n"
                    f"📈 PnL: {saldo['pnl']:.2f} USDT\n"
                    f"🎯 Ação: {decisao.get('acao', 'WAIT')}\n"
                    f"🧠 Motivo: {decisao.get('motivo', 'Sem detalhes')}"
                )
                enviar_telegram(msg)
        except Exception as e:
            print(f"[Trade] Erro na rotina: {e}")
        time.sleep(TEMPO_TRADE)


# ================= TELEGRAM LOOP (MAIN) =================
def gerar_resposta(texto):
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
    if contexto:
        prompt += f"Contexto da web:\n{contexto}\n"

    resp = chamar_ia(prompt, contexto)
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
    else:
        resp = gerar_resposta(texto)

    enviar_telegram(resp, chat_id=chat_id)


def main():
    if not TG_TOKEN:
        print("Erro: TELEGRAM_BOT_TOKEN não configurado.")
        return

    print(resumo_provedores(), flush=True)

    if ZEUS_MODO == "janela":
        base = f"https://api.telegram.org/bot{TG_TOKEN}"
        try:
            requests.get(f"{base}/deleteWebhook", timeout=10)
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
                r = requests.get(f"{base}/getUpdates", params=params, timeout=restante + 15)
                if r.status_code == 409:
                    print("ERRO 409: outro processo já está lendo este bot. Encerrando janela.")
                    break
                if r.status_code != 200:
                    time.sleep(2)
                    continue
                for att in r.json().get("result", []):
                    offset = att["update_id"] + 1
                    tratar_update(att)
            except Exception as e:
                print(f"Erro na janela Telegram: {e}")
                break

        if offset:
            try:
                requests.get(f"{base}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=15)
            except Exception:
                pass
        return

    threading.Thread(target=rotina_estudo, daemon=True).start()
    threading.Thread(target=rotina_trade, daemon=True).start()

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
                print("Aviso 409: outro leitor ativo no mesmo token; aguardando 10s...")
                time.sleep(10)
                continue
            if r.status_code != 200:
                time.sleep(3)
                continue

            for att in r.json().get("result", []):
                offset = att["update_id"] + 1
                tratar_update(att)

        except Exception as e:
            print(f"Erro no loop do Telegram: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
