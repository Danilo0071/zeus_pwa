import sqlite3, json, os
from contextlib import contextmanager

DB_PATH = "zeus_memory.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try: yield conn
    finally: conn.close()

def add_message(session_id, role, content):
    with get_db() as conn:
        conn.execute("INSERT INTO history (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))

def get_recent_context(session_id, limit=6):
    """Retorna últimas N mensagens para economizar tokens."""
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT role, content FROM history WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        )
        rows = cursor.fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def get_summary(session_id):
    """Busca um resumo (se existir) ou retorna vazio para economizar tokens."""
    # Implementação simplificada: retorna vazio se não houver resumo salvo.
    # Em produção, você salvaria o resumo gerado pelo LLM aqui.
    return ""