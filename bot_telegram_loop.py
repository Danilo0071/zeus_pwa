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
from datetime import datetime, timezone

# ================= CONFIGURAÇÕES =================
REPO_PADRAO = os.getenv("GITHUB_REPO", "Danilo0071/zeus_pwa").strip()
BRANCH_PADRAO = os.getenv("GITHUB_BRANCH", "main").strip()

# B2 (S3-Compatible)
B2_KEY_ID = os.getenv("B2_KEY_ID", "").strip().strip('"')
B2_APP_KEY = os.getenv("B2_APPLICATION_KEY", os.getenv("B2_APP_KEY", "")).strip().strip('"')
B2_BUCKET = os.getenv("B2_BUCKET", os.getenv("B2_BUCKET_NAME", "")).strip().strip('"')
B2_ENDPOINT = os.getenv("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com").strip().strip('"')

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

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"')
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"')

TEMPO_ESTUDO = 300
TEMPO_TRADE = 300

# ================= B2 (API S3-COMPATIBLE VIA REQUESTS) =================
def b2_s3_headers(method, filename, payload=None):
    t = datetime.now(timezone.utc)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')
    
    host = B2_ENDPOINT.replace("https://", "").replace("http://", "")
    
    kDate = hmac.new(("AWS4" + B2_APP_KEY).encode('utf-8'), date_stamp.encode('utf-8'), hashlib.sha256).digest()
    kRegion = hmac.new(kDate, b'us-east-005', hashlib.sha256).digest()
    kService = hmac.new(kRegion, b's3', hashlib.sha256).digest()
    kSigning = hmac.new(kService, b'aws4_request', hashlib.sha256).digest()
    
    path = f"/{B2_BUCKET}/{filename}"
    payload_hash = hashlib.sha256(payload if payload else b'').hexdigest()
    
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    
    canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    credential_scope = f"{date_stamp}/us-east-005/s3/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    
    signing_key = hmac.new(kSigning, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    auth_header = f"AWS4-HMAC-SHA256 Credential={B2_KEY_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signing_key}"
    
    return {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": auth_header,
        "Content-Type": "application/json"
    }

def b2_salvar(nome_arquivo, dados):
    try:
        corpo = json.dumps(dados, ensure_ascii=False).encode('utf-8')
        headers = b2_s3_headers("PUT", nome_arquivo, corpo)
        # A CORREÇÃO ESTÁ AQUI: Força o envio do tamanho do arquivo
        headers["Content-Length"] = str(len(corpo))
        
        url = f"{B2_ENDPOINT}/{B2_BUCKET}/{nome_arquivo}"
        
        r = requests.put(url, headers=headers, data=corpo, timeout=15)
        if r.status_code == 200:
            print(f"✅ Salvo no B2: {nome_arquivo}")
            return True
        else:
            print(f"❌ Erro ao salvar no B2 ({r.status_code}): {r.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Erro B2 salvar: {e}")
        return False

def b2_listar(prefixo):
    try:
        # Para o Backblaze S3, a listagem é um GET na raiz do bucket com parâmetros
        params = f"list-type=2&prefix={urllib.parse.quote(prefixo)}&max-keys=50"
        # O path para assinar é apenas a barra "/" na raiz, a query vai na URL
        headers = b2_s3_headers("GET", f"?{params}")
        url = f"{B2_ENDPOINT}/{B2_BUCKET}?{params}"
        
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            # Parser simples de XML para pegar as chaves dos arquivos
            matches = re.findall(r'<Key>(.*?)</Key>', r.text)
            return [{"fileName": m} for m in matches]
    except Exception: pass
    return []

def b2_baixar(nome_arquivo):
    try:
        headers = b2_s3_headers("GET", nome_arquivo)
        url = f"{B2_ENDPOINT}/{B2_BUCKET}/{nome_arquivo}"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception: pass
    return None

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
    for modelo in ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]:
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

# ================= MÓDULO DE ESTUDO =================
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
        b2_salvar(nome, dados)

def rotina_estudo():
    temas = [
        "Alavancagem e gerenciamento de risco em cripto",
        "Análise técnica: suporte, resistência e tendências",
        "Estratégias de trade para iniciantes na Binance Futures",
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
    except Exception: return None

def consultar_saldo():
    conta = binance_request("GET", "/fapi/v2/account")
    if not conta: return None
    return {
        "saldo": float(conta.get("totalWalletBalance", 0)),
        "pnl": float(conta.get("totalUnrealizedProfit", 0))
    }

def analisar_e_operar():
    arquivos = b2_listar("memoria/")
    estrategia = "Estratégia padrão: seguir tendência e usar stop curto."
    if arquivos:
        recente = b2_baixar(arquivos[-1]["fileName"])
        if recente: estrategia = recente.get("resumo", estrategia)[:500]
    
    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": BINANCE_SYMBOL}, assinado=False)
    if not ticker or "price" not in ticker: 
        return {"acao": "WAIT", "motivo": "Falha ao obter preço da Binance"}
    preco = float(ticker["price"])
    
    prompt = f"""Você é o Zeus Trader. Decida se deve ABRIR (BUY/SELL) ou AGUARDAR (WAIT).
    Estratégia da memória: {estrategia}
    Preço atual do {BINANCE_SYMBOL}: {preco}
    Responda APENAS um JSON válido com: {{"acao": "BUY"|"SELL"|"WAIT", "motivo": "texto curto", "tamanho": 0.001}}"""
    
    resp = chamar_gemini(prompt, json_mode=True)
    try:
        decisao = json.loads(resp)
    except:
        decisao = {"acao": "WAIT", "motivo": "IA indisponível no momento", "tamanho": 0}
    
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
