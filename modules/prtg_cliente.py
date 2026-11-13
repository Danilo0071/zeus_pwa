import os
import requests

# ATENÇÃO: A API do PRTG usa passhash. Obtenha em Setup > Account Settings > Passhash.
# Endpoint para editar sensor: /editsettings?id=SENSOR_ID&name_=NOVO_NOME...
# Para criar sensor, a API do PRTG é limitada. O fluxo comum é duplicar via API ou usar 'auto-discovery'.

def execute_prtg_action(sensor_id, action_type, payload):
    base = os.environ.get("PRTG_BASE_URL")
    user = os.environ.get("PRTG_USERNAME")
    passhash = os.environ.get("PRTG_PASSHASH")

    if not all([base, user, passhash]):
        return False, "Credenciais PRTG ausentes."

    # Exemplo: Ação de renomear sensor (ajuste conforme necessidade)
    # A API do PRTG aceita parâmetros via GET/POST.
    url = f"{base}/editsettings"
    
    if action_type == "rename":
        params = {
            "id": sensor_id,
            "name_": payload.get("new_name"),
            "username": user,
            "passhash": passhash
        }
    elif action_type == "pause":
        params = {
            "id": sensor_id,
            "active_": "0", # 0 = Pausado, 1 = Ativo
            "username": user,
            "passhash": passhash
        }
    else:
        return False, "Ação não suportada pelo agente no momento."

    try:
        r = requests.get(url, params=params, timeout=10, verify=False) # verify=False se for certificado self-signed
        if r.status_code == 200:
            return True, f"Configuração aplicada no sensor {sensor_id}."
        return False, f"Falha na API: {r.status_code}"
    except Exception as e:
        return False, str(e)