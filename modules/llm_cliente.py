import os
import google.generativeai as genai
from groq import Groq
import anthropic
# Corrigido para importar corretamente do arquivo database.py
from .database import get_recent_context, get_summary

def get_llm_response(session_id, user_input, model_choice="gemini"):
    # 1. Otimização de Tokens: Apenas últimas 4 mensagens + Resumo
    history = get_recent_context(session_id, limit=4)
    summary = get_summary(session_id)
    
    system_prompt = f"Você é Zeus, um assistente de rede e DevOps. Resumo do contexto: {summary}. Seja conciso."
    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_input})

    try:
        if model_choice == "gemini":
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            model = genai.GenerativeModel('gemini-2.5-flash')
            # Gemini usa formato diferente
            chat = model.start_chat(history=[])
            # Simplificação: enviar o histórico concatenado para o Gemini
            full_prompt = system_prompt + "\n" + "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            resp = chat.send_message(full_prompt)
            return resp.text

        elif model_choice == "groq":
            client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            completion = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
                temperature=0.3
            )
            return completion.choices[0].message.content

        elif model_choice == "claude":
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            # Claude exige system prompt separado
            claude_msgs = [m for m in messages if m["role"] != "system"]
            resp = client.messages.create(
                model="claude-haiku-5-5",
                max_tokens=1024,
                system=system_prompt,
                messages=claude_msgs
            )
            return resp.content[0].text
    except Exception as e:
        return f"Erro no modelo {model_choice}: {str(e)}"