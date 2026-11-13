import os
import requests
import time
from .prtg_client import execute_prtg_action

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "zeus_default")

def send_alert(title, message):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message, headers={"Title": title, "Priority": "high"})
    except: pass

def check_logs_and_act(log_path="network_monitor.log"):
    """Lê log e dispara alerta se houver erro crítico."""
    if not os.path.exists(log_path):
        return
    with open(log_path, "r") as f:
        lines = f.readlines()
        if lines:
            last = lines[-1]
            if "ERROR" in last or "DOWN" in last:
                send_alert("Alerta de Rede Zeus", f"Status detectado: {last.strip()}")