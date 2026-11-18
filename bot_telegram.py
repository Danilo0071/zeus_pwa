import os
import time
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def get_resposta_ia(texto_usuario):
    if not GROQ_API_KEY:
        return "⚠️ Chave da IA não configurada no bot."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {"role": "system", "content": "Você é Zeus, um assistente autônomo e inteligente."},
            {"role": "user", "content": texto_usuario}
        ]
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        return "Não consegui processar sua mensagem agora."
    except Exception as e:
        return f"Erro de conexão: {e}"

def escutar_telegram():
    if not TELEGRAM_TOKEN:
        print("❌ Token do Telegram não configurado.")
        return

    offset = 0
    url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    print("🤖 Zeus Bot iniciado no GitHub Actions.")
    
    tempo_inicio = time.time()
    limite_tempo = 5 * 60 * 60

    while time.time() - tempo_inicio < limite_tempo:
        try:
            r = requests.get(f"{url_base}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35)
            if r.status_code == 200:
                data = r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message") or update.get("edited_message")
                    if not msg: continue
                    
                    chat_id = msg["chat"]["id"]
                    texto = msg.get("text", "").strip()
                    if not texto: continue
                    
                    print(f"Recebido: {texto}")
                    resposta = get_resposta_ia(texto)
                    
                    requests.post(f"{url_base}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⚡ [ZEUS IA]\n\n{resposta}",
                        "parse_mode": "Markdown"
                    }, timeout=15)
        except Exception as e:
            print(f"Erro no loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    escutar_telegram()
