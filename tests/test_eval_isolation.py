"""Itens 1 e 2 da auditoria da Getnet v3 (31/10/2026): isolamento entre casos e Store aquecido antes da leva.

1. Todos os casos usam o mesmo user_id num banco só; a memória de longo prazo é por usuário, então um caso
   citou o valor de outro. Cada caso precisa começar sem a memória dos anteriores, sem trocar o user_id
   (o CRM é por usuário: trocar o id quebraria as tools).
2. A 1ª leva paralela criava o Store dentro dos workers: "database is locked" nos primeiros casos.
"""
import threading

import pytest

from evals import run_evals
from evals.run_evals import run


class MemorySpy:
    """Cliente em processo que anota a memória de longo prazo encontrada no 1º turno de cada caso."""

    def __init__(self):
        self.inner = run_evals.Client(None)
        self.seen: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def send(self, message, user_id, conversation_id):
        from app.store import get_store

        if conversation_id is None:  # 1º turno do caso: nada de casos anteriores pode estar aqui
            with self.lock:
                self.seen.append((message, get_store().user_summary(user_id)))
        return self.inner.send(message, user_id, conversation_id)


@pytest.mark.parametrize("workers", [1, 4])
def test_each_case_starts_without_memory_of_other_cases(tmp_path, workers):
    spy = MemorySpy()
    report = run(out=tmp_path / "latest.json", workers=workers, repeat=2, client=spy)
    leaked = [(m, s) for m, s in spy.seen if s]
    assert spy.seen and not leaked, leaked
    # o CRM continua sendo do usuário do caso: a consulta ao pedido 1002 do u1 segue funcionando
    lookup = next(r for r in report["rows"] if r["id"] == "official-03")
    assert lookup["checks"]["tools"] is True
    assert report["isolation"] == "db_per_case"


def test_memory_inside_a_case_still_works(tmp_path):
    # o caso de memória (before + seguimento) depende do histórico da PRÓPRIA conversa: isolar não pode quebrar
    report = run(out=tmp_path / "latest.json", split="memory")
    assert report["metrics"]["memory"]["intent_acc"] == 100.0, report["failures"]


def test_store_is_created_before_the_parallel_wave(monkeypatch, tmp_path):
    from app import store

    first_init: dict[str, bool] = {}
    original = store.Store.__init__

    def spy(self, path):
        first_init.setdefault(str(path), threading.current_thread() is threading.main_thread())
        original(self, path)

    monkeypatch.setattr(store.Store, "__init__", spy)
    run(out=tmp_path / "latest.json", workers=4)
    created_in_worker = [p for p, main in first_init.items() if not main]
    assert first_init and not created_in_worker, created_in_worker
