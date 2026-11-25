import json
import os
import time
import requests

STATE_FILE = "tg_state.json"


def _carregar_ultimo_id() -> int:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get("last_update_id", 0))
    except Exception:
        pass
    return 0


def _salvar_ultimo_id(update_id: int) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_update_id": int(update_id)}, f)
    except Exception as e:
        print(f"Aviso ao salvar estado: {e}")


def _chamar_gemini(texto: str, gemini_key: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_key}"
    )
    payload = {
        "systemInstruction": {
            "parts": [
                {
                    "text": "Você é o Zeus. Responda de forma direta, objetiva e em português."
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": texto}],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 600,
        },
    }
    r = requests.post(url, json=payload, timeout=25)
    if r.status_code == 200:
        parts = (
            r.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        textos = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
        return "".join(textos).strip()
    print(f"Erro Gemini ({r.status_code}): {r.text[:200]}")
    return ""


def _chamar_groq(texto: str, groq_key: str) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {
                    "role": "system",
                    "content": "Você é o Zeus. Responda de forma direta, objetiva e em português.",
                },
                {"role": "user", "content": texto},
            ],
            "max_tokens": 600,
        },
        timeout=25,
    )
    if r.status_code == 200:
        return (
            r.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    print(f"Erro Groq ({r.status_code}): {r.text[:200]}")
    return ""


def _gerar_resposta(texto: str) -> str:
    if texto == "/start":
        return "Zeus ativo em tempo real no Telegram. Pode mandar."

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    if gemini_key:
        try:
            resposta = _chamar_gemini(texto, gemini_key)
            if resposta:
                return resposta
        except Exception as e:
            print(f"Exceção Gemini: {e}")

    if groq_key:
        try:
            resposta = _chamar_groq(texto, groq_key)
            if resposta:
                return resposta
        except Exception as e:
            print(f"Exceção Groq: {e}")

    return f"Recebido: {texto}"


def _enviar_mensagem(token: str, chat_id: str, texto: str) -> None:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto[:4000]},
            timeout=25,
        )
        ok = r.status_code == 200 and bool(r.json().get("ok"))
        print(f"Envio para {chat_id}: {'OK' if ok else r.text[:200]}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id_autorizado = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        print("ERRO: TELEGRAM_BOT_TOKEN não configurado.")
        return

    try:
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
    except Exception:
        pass

    ultimo_id = _carregar_ultimo_id()
    print("Zeus escutando mensagens em tempo real (Gemini)...")

    while True:
        params = {"timeout": 20}
        if ultimo_id > 0:
            params["offset"] = ultimo_id + 1

        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params=params,
                timeout=30,
            )
            dados = resp.json()
        except Exception as e:
            print(f"Erro no getUpdates: {e}")
            time.sleep(2)
            continue

        if not dados.get("ok"):
            print(f"API Telegram retornou erro: {dados}")
            time.sleep(3)
            continue

        for upd in dados.get("result", []):
            uid = int(upd.get("update_id", 0))
            if uid > ultimo_id:
                ultimo_id = uid
                _salvar_ultimo_id(ultimo_id)

            msg = upd.get("message") or upd.get("edited_message") or {}
            texto = (msg.get("text") or "").strip()
            cid = str((msg.get("chat") or {}).get("id") or "").strip()

            if not texto or not cid:
                continue

            if chat_id_autorizado and cid != chat_id_autorizado:
                print(f"Ignorado chat_id {cid} (esperado {chat_id_autorizado})")
                continue

            resposta = _gerar_resposta(texto)
            _enviar_mensagem(token, cid, resposta)


if __name__ == "__main__":
    main()
