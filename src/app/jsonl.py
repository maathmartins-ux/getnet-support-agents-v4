"""Família de JSONL: `<nome>.jsonl` + `<nome>.d/*.jsonl`, nesta ordem.

Kit 3.6.0: cada fork escreve o próprio arquivo em `<nome>.d/` (posse sem conflito de merge) e quem lê vê a
família inteira. A base continua valendo: o kit e os casos oficiais ficam no arquivo de sempre.
"""
from __future__ import annotations

import json
from pathlib import Path


def family(path: Path) -> list[Path]:
    path = Path(path)
    extra = path.parent / f"{path.stem}.d"
    return ([path] if path.exists() else []) + (sorted(extra.glob("*.jsonl")) if extra.is_dir() else [])


def read_family(path: Path) -> list[dict]:
    return [json.loads(line) for p in family(path)
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
