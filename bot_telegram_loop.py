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

# ================= CONFIGURAÇÕES (PUXANDO DO SEU AMBIENTE) =================
REPO_PADRAO = os.getenv("GITHUB_REPO", "Danilo0071/zeus_pwa").strip()
BRANCH_PADRAO = os.getenv("GITHUB_BRANCH", "main").strip()

# B2 (Ajustado para os nomes das suas fotos)
B2_KEY_ID = os.getenv("B2_KEY_ID", "").strip()
B2_APP_KEY = os.getenv("B2_APPLICATION_KEY", "").strip()
B2_BUCKET = os.getenv("B2_BUCKET_NAME", os.getenv("B2_BUCKET", "")).strip()

# Binance Demo (Ajustado conforme fotos)
BINANCE_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_USE_DEMO = os.getenv("BINANCE_USE_DEMO", "true").lower() == "true"
BINANCE_MAX_LEVERAGE = float(os.getenv("BINANCE_MAX_LEVERAGE", "2"))
BINANCE_BASE_URL = "https://demo-fapi.binance.com" if BINANCE_USE_DEMO else "https://fapi.binance.com"
BINANCE_SYMBOL = "BTCUSDT"

# IA (Ajustado o Tavily para minúsculo, igual na sua foto)
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROK_KEY = os.getenv("GROK_API_KEY", "").strip()
TAVILY_KEY = os.getenv("tavily_api_key", os.getenv("TAVILY_API_KEY", "")).strip()

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Tempos (em segundos) - Mudei para 5 minutos para você ver a ação mais rápido nos testes
TEMPO_ESTUDO = 300   # Estuda um tema novo a cada 5 minutos
TEMPO_TRADE = 300    # Operação e relatório a cada 5 minutos (mude para 3600 depois que validar)
TEMPO_RELATORIO = 300

# ================= B2 (API REST NATIVA - SEM SDK) =================
_b2_auth = {}

def b2_authorize():
    global _b2_auth
    if _b2_auth.get("token") and _b2_auth.get("expires", 0) > time.time():
        return _b2_auth
    if not B2_KEY_ID or not B2_APP_KEY: return None
    try:
        b64 = base64.b64encode(f"{B2_KEY_ID}:{B2_APP_KEY}".encode()).decode()
        r = requests.get("https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
                         headers={"Authorization": f"Basic {b64}"}, timeout=15)
        if r.status_code == 200:
            d = r.json()
            _b2_auth = {"token": d["authorizationToken"], "api_url": d["apiUrl"],
                        "download_url": d["downloadUrl"], "expires": time.time() + 3600}
            return _b2_auth
        else:
            print(f"Erro B2 Auth: {r.status_code} - {r.text[:100]}")
    except Exception as e: print(f"B2 auth erro: {e}")
    return None

def b2_listar(prefixo):
    auth = b2_authorize()
    if not auth: return []
    try:
        r = requests.post(f"{auth['api_url']}/b2api/v2/b2_list_file_names",
            headers={"Authorization": auth["token"]},
            json={"bucketId": B2_BUCKET, "prefix": prefixo, "maxFileCount": 50}, timeout=15)
        if r.status_code == 200: return r.json().get("files", [])
        else: print(f"Erro B2 Listar: {r.text[:100]}")
    except Exception: pass
    return []

def b2_baixar(nome_arquivo):
    auth = b2_authorize()
    if not auth: return None
    try:
        url = f"{auth['download_url']}/file/{B2_BUCKET}/{urllib.parse.quote(nome_arquivo)}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200: return r.json()
    except Exception: pass
    return None

def b2_salvar(nome_arquivo, dados):
    auth = b2_authorize()
    if not auth: return False
    try:
        r = requests.post(f"{auth['api_url']}/b2api/v2/b2_get_upload_url",
            headers={"Authorization": auth["token"]},
            json={"bucketId": B2_BUCKET}, timeout=15)
        if r.status_code != 200: 
            print(f"Erro B2 Upload URL: {r.text[:100]}")
            return False
        up = r.json()
        corpo = json.dumps(dados, ensure_ascii=False).encode('utf-8')
        sha1 = hashlib.sha1(corpo).hexdigest()
        headers = {"Authorization": up["authorizationToken"],
                   "X-Bz-File-Name": urllib.parse.quote(nome_arquivo),
                   "Content-Type": "application/json", "X-Bz-Content-Sha1": sha1}
        r2 = requests.post(up["uploadUrl"], headers=headers, data=corpo, timeout=20)
        return r2.status_code == 200
    except Exception as e:
        print(f"B2 salvar erro: {e}")
        return False

# ================= BUSCA WEB E IA =================
def buscar_web(consulta):
    if TAVILY_KEY:
        try:
            r = requests.post("https://api.tavily.com/search", json={
                "api_key": TAVILY_KEY, "query": consulta, "max_results": 3, "include_answer": True
            }, timeout=12)
            if r.status_code == 200:
                d = r.json()
                partes = []
                if d.get("answer"): partes.append(d["answer"])
                for item in d.get("results", [])[:2]:
                    partes.append(f"- {item.get('title','')}: {item.get('content','')[:300]}")
                if partes: return "\n".join(partes)
        except Exception: pass
    try:
        q = urllib.parse.quote_plus(consulta)
        r = requests.get(f"https://html.duckduckgo.com/html/?q={q}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            titulos = re.findall(r'class="result__a"[^>]>(.?)</a>', r.text, re.S)[:2]
            trechos = re.findall(r'class="result__snippet"[^>]>(.?)</a>', r.text, re.S)[:2]
            linhas = []
            for t, s in zip(titulos, trechos):
                tl = html.unescape(re.sub(r"<.*?>", "", t)).strip()
                sl = html.unescape(re.sub(r"<.*?>", "", s)).strip()
                if tl: linhas.append(f"- {tl}: {sl}")
            return "\n".join(linhas) if linhas else ""
    except Exception: pass
    return ""

def chamar_gemini(prompt, json_mode=False):
    if not GEMINI_KEY: return None
    # Modelos estáveis para contas novas (evita 404)
    for modelo in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1000}}
        if json_mode: payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(url, json=payload, timeout=25)
            if r.status_code == 200:
                cands = r.json().get("candidates", [])
                if cands:
                    txt = "".join(p.get("text","") for p in cands[0].get("content",{}).get("parts",[])).strip()
                    if txt: return txt
            else:
                print(f"Aviso Gemini ({modelo}): {r.status_code}")
        except Exception: pass
    return None

def chamar_grok(prompt):
    if not GROK_KEY: return None
    try:
        r = requests.post("https://api.x.ai/v1/chat/completions", headers={
            "Authorization": f"Bearer {GROK_KEY}", "Content-Type": "application/json"
        }, json={"model": "grok-2-latest", "messages": [{"role": "user", "content": prompt}]}, timeout=25)
        if r.status_code == 200:
            return r.json().get("choices",[{}])[0].get("message",{}).get("content","").strip()
    except Exception: pass
    return None

# ================= MÓDULO DE ESTUDO (BACKGROUND) =================
def estudar_e_salvar(tema):
    print(f"[Estudo] Buscando conhecimento sobre: {tema}")
    contexto = buscar_web(tema)
    prompt = (
        f"Você é um trader especialista. Crie um guia prático e direto em português do Brasil sobre: {tema}.\n"
        "Foque em regras de entrada, saída, gestão de risco e alavancagem segura.\n"
    )
    if contexto: prompt += f"Contexto atual:\n{contexto}\n"
    
    resp = chamar_gemini(prompt) or chamar_grok(prompt)
    if resp:
        dados = {"topico": tema, "resumo": resp, "criado_em": int(time.time())}
        nome = f"memoria/{tema.lower().replace(' ', '')[:30]}{int(time.time())}.json"
        if b2_salvar(nome, dados):
            print(f"[Estudo] Salvo no B2: {nome}")

def rotina_estudo():
    temas = [
        "Alavancagem e gerenciamento de risco em cripto",
        "Análise técnica: suporte, resistência e tendências",
        "Estratégias de trade para iniciantes na Binance Futures",
        "Como usar stop loss e take profit com segurança",
        "Psicologia do trading e controle emocional"
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
    if not BINANCE_KEY or not BINANCE_SECRET: return None
    if params is None: params = {}
    if assinado:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{BINANCE_BASE_URL}{endpoint}?{query}&signature={sig}"
    else:
        url = f"{BINANCE_BASE_URL}{endpoint}"
    
    try:
        if method == "GET": r = requests.get(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=15)
        else: r = requests.post(url, headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=15)
        return r.json() if r.status_code in (200, 400) else None
    except Exception as e: 
        print(f"[Binance] Erro: {e}")
        return None

def consultar_saldo():
    conta = binance_request("GET", "/fapi/v2/account")
    if not conta: 
        # Tenta buscar o erro exato da Binance Demo
        if "msg" in conta: print(f"[Binance] {conta['msg']}")
        return None
    return {
        "saldo": float(conta.get("totalWalletBalance", 0)),
        "pnl": float(conta.get("totalUnrealizedProfit", 0))
    }

def analisar_e_operar():
    # 1. Busca conhecimento no B2 para decidir (Não gasta cota de IA)
    arquivos = b2_listar("memoria/")
    estrategia = "Estratégia padrão: seguir tendência e usar stop curto."
    if arquivos:
        recente = b2_baixar(arquivos[-1]["fileName"])
        if recente: estrategia = recente.get("resumo", estrategia)[:500]
    
    # 2. Pega dados de mercado
    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": BINANCE_SYMBOL}, assinado=False)
    if not ticker or "price" not in ticker: 
        return {"acao": "WAIT", "motivo": "Falha ao obter preço da Binance"}
    preco = float(ticker["price"])
    
    # 3. LLM decide com base no que aprendeu
    prompt = f"""Você é o Zeus Trader. Decida se deve ABRIR (BUY/SELL) ou AGUARDAR (WAIT).
    Estratégia da memória: {estrategia}
    Preço atual do {BINANCE_SYMBOL}: {preco}
    Responda APENAS um JSON válido com: {{"acao": "BUY"|"SELL"|"WAIT", "motivo": "texto curto", "tamanho": 0.001}}"""
    
    resp = chamar_gemini(prompt, json_mode=True)
    try:
        decisao = json.loads(resp)
    except:
        decisao = {"acao": "WAIT", "motivo": "IA indisponível no momento", "tamanho": 0}
    
    # 4. Executa ordem
    log_acao = {"preco": preco, "decisao": decisao, "ts": int(time.time())}
    if decisao.get("acao") in ["BUY", "SELL"]:
        binance_request("POST", "/fapi/v1/leverage", {"symbol": BINANCE_SYMBOL, "leverage": int(BINANCE_MAX_LEVERAGE)})
        ordem = binance_request("POST", "/fapi/v1/order", {
            "symbol": BINANCE_SYMBOL, "side": decisao["acao"], 
            "type": "MARKET", "quantity": decisao.get("tamanho", 0.001)
        })
        log_acao["ordem"] = ordem
        b2_salvar(f"trades/log_{int(time.time())}.json", log_acao)
    
    return decisao

def enviar_telegram(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": msg}, timeout=15)
    except Exception: pass

def rotina_trade():
    while True:
        try:
            print("[Trade] Iniciando ciclo de análise e operação...")
            decisao = analisar_e_operar()
            saldo = consultar_saldo()
            
            if saldo:
                msg = (f"🤖 Relatório Zeus Trader\n"
                       f"💰 Saldo: {saldo['saldo']:.2f} USDT\n"
                       f"📈 PnL: {saldo['pnl']:.2f} USDT\n"
                       f"🎯 Ação: {decisao['acao']}\n"
                       f"🧠 Motivo: {decisao['motivo']}")
                enviar_telegram(msg)
            else:
                print("[Trade] Saldo não obtido (A Binance Demo pode estar com instabilidade).")
        except Exception as e:
            print(f"[Trade] Erro na rotina: {e}")
        time.sleep(TEMPO_TRADE)

# ================= TELEGRAM LOOP (MAIN) =================
def gerar_resposta(texto):
    # Tenta achar resposta na memória B2 primeiro
    arquivos = b2_listar("memoria/")
    for arq in reversed(arquivos[-5:]):
        dados = b2_baixar(arq["fileName"])
        if dados and texto.lower() in dados.get("topico", "").lower():
            return f"📚 Da minha memória:\n\n{dados['resumo']}"
    
    contexto = buscar_web(texto)
    prompt = f"VOCÊ É O ZEUS. Responda SEMPRE em português do Brasil, de forma direta e amigável.\nUsuário: {texto}\n"
    if contexto: prompt += f"Contexto da web:\n{contexto}\n"
    
    resp = chamar_gemini(prompt) or chamar_grok(prompt)
    return resp or "Poxa, meus servidores estão ocupados. Tente novamente em instantes!"

def main():
    if not TG_TOKEN:
        print("Erro: TELEGRAM_BOT_TOKEN não configurado.")
        return

    # Inicia as rotinas em segundo plano
    threading.Thread(target=rotina_estudo, daemon=True).start()
    threading.Thread(target=rotina_trade, daemon=True).start()

    offset = None
    print("Zeus online (Telegram + Estudo + Trade + B2).")

    while True:
        try:
            params = {"timeout": 20}
            if offset: params["offset"] = offset
            
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates", params=params, timeout=25)
            if r.status_code != 200:
                time.sleep(3); continue
            
            for att in r.json().get("result", []):
                offset = att["update_id"] + 1
                msg = att.get("message") or att.get("edited_message") or {}
                texto = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                
                if not texto or not chat_id: continue
                if TG_CHAT_ID and chat_id != TG_CHAT_ID: continue
                
                # Comandos
                texto_lower = texto.lower()
                if texto_lower == "status trade":
                    s = consultar_saldo()
                    resp = f"💰 Saldo: {s['saldo']:.2f} | 📈 PnL: {s['pnl']:.2f}" if s else "❌ Erro ao acessar Binance Demo (verifique as chaves)."
                elif texto_lower == "estudar agora":
                    threading.Thread(target=estudar_e_salvar, args=("Estratégias avançadas de price action",), daemon=True).start()
                    resp = "🧠 Iniciei um novo ciclo de estudos em segundo plano!"
                else:
                    resp = gerar_resposta(texto)
                
                enviar_telegram(resp)
                
        except Exception as e:
            print(f"Erro no loop do Telegram: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
