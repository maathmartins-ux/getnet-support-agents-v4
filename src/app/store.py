"""Estado persistido em SQLite: conversa, fila humana, ações, tokens revogados, limite de uso e analytics.

Sobrevive a restart e é compartilhado por todos os processos que leem o mesmo volume (WAL + busy timeout).
Toda transição de estado disputada é um UPDATE condicional (`... WHERE status = 'x'`, rowcount == 1): a
resposta do operador, a aprovação, a confirmação e o re-escalonamento por SLA acontecem uma vez só, também
entre réplicas. Para várias máquinas, troque por Postgres atrás desta mesma interface (é o único módulo
que conhece SQL).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .config import action_ttl_min, db_path, sla_minutes

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
  role TEXT NOT NULL, content TEXT NOT NULL, intent TEXT, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL, reason TEXT NOT NULL,
  summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', answer TEXT,
  created_at REAL NOT NULL, answered_at REAL);
CREATE TABLE IF NOT EXISTS user_memory (
  user_id TEXT PRIMARY KEY, summary TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT NOT NULL, tool TEXT NOT NULL,
  args TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', result TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revoked_tokens (jti TEXT PRIMARY KEY, exp REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rate_hits (key TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_rate_key ON rate_hits(key, ts);
CREATE TABLE IF NOT EXISTS requests (
  trace_id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, intent TEXT, route TEXT, handoff INTEGER,
  blocked INTEGER, ungrounded INTEGER, cost_usd REAL, ms REAL, message TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (
  trace_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT, ts REAL NOT NULL);
"""
# Migração aditiva (banco de uma versão anterior no volume): coluna nova entra sem perder histórico.
COLUMNS = {
    "tickets": {"answered_by": "TEXT", "priority": "TEXT NOT NULL DEFAULT 'normal'",
                "kind": "TEXT NOT NULL DEFAULT 'handoff'", "action_id": "TEXT", "due_at": "REAL",
                "escalation_level": "INTEGER NOT NULL DEFAULT 0"},
    "actions": {"risk": "TEXT NOT NULL DEFAULT 'normal'", "expires_at": "REAL", "approved_by": "TEXT",
                "ticket_id": "TEXT"},
}

# Trocar o modo de journal para WAL pede lock exclusivo e o SQLite NÃO chama o busy handler nesse PRAGMA:
# com outra conexão aberta no mesmo arquivo novo, ele devolve "database is locked" na hora. Getnet v3
# (31/10/2026): 4 threads do eval ao vivo criaram o banco juntas e 7 respostas caíram. Retry curto resolve
# também entre processos (réplicas), onde a trava de thread de get_store() não alcança.
WAL_DEADLINE_S = 5.0


def _enable_wal(conn: sqlite3.Connection) -> None:
    deadline = time.monotonic() + WAL_DEADLINE_S
    delay = 0.005
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            _enable_wal(conn)  # leitores não bloqueiam o escritor (vários processos)
            # Esquema e migração numa transação IMMEDIATE: quem chega depois espera (busy_timeout) e já vê as
            # colunas; sem isso, dois processos leem "coluna falta" e o segundo ALTER quebra (duplicate column).
            conn.execute("BEGIN IMMEDIATE")
            for statement in filter(str.strip, SCHEMA.split(";")):
                conn.execute(statement)
            for table, cols in COLUMNS.items():
                have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in cols.items():
                    if name not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            # Um caso de atendimento aberto por conversa, garantido PELO BANCO: com 2 réplicas, "existe? senão cria"
            # em dois passos deixava as duas criarem (achado ao montar o teste de réplicas, 01/11/2026).
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_open_handoff ON tickets(conversation_id) "
                             "WHERE status = 'open' AND kind = 'handoff'")
            except sqlite3.IntegrityError:  # banco antigo com duplicata: segue sem o índice (o código ainda checa)
                pass

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=10)  # espera o lock de outro processo em vez de falhar
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")  # padrão seguro com WAL: sem fsync por commit, sem corromper
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- conversa ---------------------------------------------------------------------
    def add_message(self, conversation_id: str, user_id: str, role: str, content: str,
                    intent: str | None = None) -> None:
        with self._db() as conn:
            conn.execute("INSERT INTO messages (conversation_id, user_id, role, content, intent, ts) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (conversation_id, user_id, role, content, intent, time.time()))

    def history(self, conversation_id: str, limit: int = 20) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute("SELECT role, content, intent, ts FROM messages WHERE conversation_id = ? "
                                "ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def older_messages(self, conversation_id: str, skip_last: int) -> list[dict]:
        """Mensagens que já saíram da janela recente (para o resumo rolante da conversa)."""
        with self._db() as conn:
            rows = conn.execute("SELECT role, content, intent FROM messages WHERE conversation_id = ? "
                                "ORDER BY id DESC LIMIT -1 OFFSET ?", (conversation_id, skip_last)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def conversation_owner(self, conversation_id: str) -> str | None:
        with self._db() as conn:
            row = conn.execute("SELECT user_id FROM messages WHERE conversation_id = ? ORDER BY id LIMIT 1",
                               (conversation_id,)).fetchone()
        return row["user_id"] if row else None

    # --- memória de longo prazo por usuário ------------------------------------------------
    def user_summary(self, user_id: str) -> str:
        with self._db() as conn:
            row = conn.execute("SELECT summary FROM user_memory WHERE user_id = ?", (user_id,)).fetchone()
        return row["summary"] if row else ""

    def set_user_summary(self, user_id: str, summary: str) -> None:
        with self._db() as conn:
            conn.execute("INSERT INTO user_memory (user_id, summary, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id) DO UPDATE SET summary = excluded.summary, "
                         "updated_at = excluded.updated_at", (user_id, summary[:1000], time.time()))

    # --- fila humana: handoff e aprovação -----------------------------------------------------
    def open_ticket(self, conversation_id: str, user_id: str, reason: str, summary: str,
                    priority: str = "normal", kind: str = "handoff", action_id: str | None = None) -> dict:
        if kind == "handoff":
            existing = self.ticket_for(conversation_id, status="open", kind="handoff")
            if existing:  # idempotente: a mesma conversa não abre dois casos de atendimento
                return existing
        now = time.time()
        tid = "T-" + hashlib.sha1(f"{conversation_id}|{reason}|{action_id}|{now}".encode()).hexdigest()[:8]
        try:
            with self._db() as conn:
                conn.execute("INSERT INTO tickets (id, conversation_id, user_id, reason, summary, created_at, "
                             "priority, kind, action_id, due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (tid, conversation_id, user_id, reason, summary, now, priority, kind, action_id,
                              now + 60 * sla_minutes(priority)))
        except sqlite3.IntegrityError:  # outra réplica abriu o caso desta conversa entre a checagem e o insert
            existing = self.ticket_for(conversation_id, status="open", kind="handoff")
            if existing:
                return existing
            raise
        return self.get_ticket(tid)

    def get_ticket(self, ticket_id: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return dict(row) if row else None

    def ticket_for(self, conversation_id: str, status: str = "open", kind: str | None = None) -> dict | None:
        sql, args = "SELECT * FROM tickets WHERE conversation_id = ? AND status = ?", [conversation_id, status]
        if kind:
            sql, args = sql + " AND kind = ?", [*args, kind]
        with self._db() as conn:
            row = conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def list_tickets(self, status: str = "open") -> list[dict]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM tickets WHERE status = ? ORDER BY priority = 'high' DESC, created_at",
                                (status,)).fetchall()
        return [dict(r) for r in rows]

    def close_ticket(self, ticket_id: str, answer: str, operator: str, status: str = "answered") -> dict | None:
        """Fecha o caso uma vez só (UPDATE condicional): duas respostas simultâneas → uma vence, a outra None."""
        with self._db() as conn:
            cur = conn.execute("UPDATE tickets SET status = ?, answer = ?, answered_at = ?, answered_by = ? "
                               "WHERE id = ? AND status = 'open'", (status, answer, time.time(), operator, ticket_id))
        return self.get_ticket(ticket_id) if cur.rowcount == 1 else None

    def answer_ticket(self, ticket_id: str, answer: str, operator: str = "admin") -> dict | None:
        ticket = self.close_ticket(ticket_id, answer, operator)
        if ticket:  # a resposta humana entra no histórico da conversa do cliente
            self.add_message(ticket["conversation_id"], ticket["user_id"], "human_agent", answer)
        return ticket

    def escalate_overdue(self, now: float | None = None) -> list[dict]:
        """Casos abertos com SLA vencido sobem para prioridade alta UMA vez (atômico entre réplicas)."""
        now = now or time.time()
        with self._db() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE status = 'open' AND "
                                                 "escalation_level = 0 AND due_at < ?", (now,))]
            won = [tid for tid in ids if conn.execute(
                "UPDATE tickets SET escalation_level = 1, priority = 'high' WHERE id = ? AND escalation_level = 0",
                (tid,)).rowcount == 1]
        return [self.get_ticket(t) for t in won]

    # --- ações que exigem confirmação (e aprovação, se risco alto) ------------------------------
    def create_action(self, conversation_id: str | None, user_id: str, tool: str, args: dict,
                      risk: str = "normal") -> str:
        aid = "A-" + uuid.uuid4().hex[:8]
        now = time.time()
        with self._db() as conn:
            conn.execute("INSERT INTO actions (id, conversation_id, user_id, tool, args, created_at, risk, expires_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (aid, conversation_id, user_id, tool, json.dumps(args, ensure_ascii=False), now, risk,
                          now + 60 * action_ttl_min()))
        return aid

    def get_action(self, action_id: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            return None
        action = dict(row)
        action["args"] = json.loads(action["args"])
        return action

    def claim_action(self, action_id: str, frm: str = "pending", to: str = "running") -> bool:
        """Muda o estado só se ainda estiver em `frm`. True = este chamador venceu a disputa."""
        with self._db() as conn:
            cur = conn.execute("UPDATE actions SET status = ? WHERE id = ? AND status = ?", (to, action_id, frm))
        return cur.rowcount == 1

    def update_action(self, action_id: str, **fields) -> None:
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._db() as conn:
            conn.execute(f"UPDATE actions SET {cols} WHERE id = ?", (*fields.values(), action_id))

    def finish_action(self, action_id: str, status: str, result: dict) -> None:
        self.update_action(action_id, status=status, result=result)

    # --- tokens revogados -----------------------------------------------------------------------
    def revoke(self, jti: str, exp: float) -> None:
        with self._db() as conn:
            conn.execute("INSERT OR IGNORE INTO revoked_tokens (jti, exp) VALUES (?, ?)", (jti, exp))
            conn.execute("DELETE FROM revoked_tokens WHERE exp < ?", (time.time() - 3600,))  # expirado não precisa

    def is_revoked(self, jti: str) -> bool:
        with self._db() as conn:
            return conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone() is not None

    # --- limite de uso compartilhado --------------------------------------------------------------
    def rate_allow(self, key: str, limit: int | None = None) -> bool:
        from .config import rate_limit_per_min

        limit = rate_limit_per_min() if limit is None else limit
        now = time.time()
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")  # contagem e inserção na mesma transação: sem corrida entre processos
            conn.execute("DELETE FROM rate_hits WHERE key = ? AND ts < ?", (key, now - 60))
            used = conn.execute("SELECT COUNT(*) FROM rate_hits WHERE key = ?", (key,)).fetchone()[0]
            if used >= limit:
                return False
            conn.execute("INSERT INTO rate_hits (key, ts) VALUES (?, ?)", (key, now))
        return True

    def rate_reset(self) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM rate_hits")

    # --- analytics: sistema de trabalho (cada atendimento vira dado) -------------------------------
    def log_request(self, row: dict) -> None:
        with self._db() as conn:
            conn.execute("INSERT OR REPLACE INTO requests (trace_id, conversation_id, user_id, intent, route, handoff, "
                         "blocked, ungrounded, cost_usd, ms, message, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (row["trace_id"], row.get("conversation_id"), row.get("user_id"), row.get("intent"),
                          ",".join(row.get("route", [])), int(bool(row.get("handoff"))), int(bool(row.get("blocked"))),
                          int(bool(row.get("ungrounded"))), row.get("cost_usd", 0.0), row.get("ms", 0.0),
                          row.get("message", "")[:300], time.time()))

    def request_owner(self, trace_id: str) -> str | None:
        with self._db() as conn:
            row = conn.execute("SELECT user_id FROM requests WHERE trace_id = ?", (trace_id,)).fetchone()
        return row["user_id"] if row else None

    def add_feedback(self, trace_id: str, user_id: str, rating: int, comment: str | None) -> None:
        with self._db() as conn:
            conn.execute("INSERT OR REPLACE INTO feedback (trace_id, user_id, rating, comment, ts) "
                         "VALUES (?, ?, ?, ?, ?)", (trace_id, user_id, rating, (comment or "")[:500], time.time()))

    def analytics_rows(self) -> tuple[list[dict], list[dict]]:
        with self._db() as conn:
            reqs = [dict(r) for r in conn.execute("SELECT * FROM requests ORDER BY ts")]
            fb = [dict(r) for r in conn.execute("SELECT * FROM feedback")]
        return reqs, fb


_STORE_LOCK = threading.Lock()


@lru_cache(maxsize=8)
def _store_for(path: str) -> Store:
    return Store(Path(path))


def get_store() -> Store:
    # Cache por caminho: testes trocam DB_PATH e recebem um banco novo sem reiniciar o processo.
    # lru_cache não trava: sem o lock, N threads no 1º request criam N Stores do mesmo banco ao mesmo tempo.
    path = str(db_path())
    with _STORE_LOCK:
        return _store_for(path)
