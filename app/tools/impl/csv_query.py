"""
CSV query tool. Read-only aggregate/filter over a sandboxed CSV file.
No arbitrary SQL, no eval -- just a whitelisted set of operations, because
this tool is scored data_sensitivity=2 and a flexible query language would
undercut that score's meaning.
"""
import csv
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, field_validator

from app.tools.impl.file_reader import SANDBOX_DIR

_ALLOWED_OPS = {"eq", "gt", "lt", "gte", "lte"}


class CsvQueryInput(BaseModel):
    filename: str
    column: Optional[str] = None
    op: Optional[Literal["eq", "gt", "lt", "gte", "lte"]] = None
    value: Optional[str] = None
    limit: int = 50

    @field_validator("limit")
    @classmethod
    def cap_limit(cls, v: int) -> int:
        return min(max(v, 1), 200)


def run(args: dict) -> list[dict]:
    payload = CsvQueryInput(**args)
    candidate = (SANDBOX_DIR / payload.filename).resolve()

    if SANDBOX_DIR not in candidate.parents and candidate != SANDBOX_DIR:
        raise PermissionError(f"Path escapes sandbox: '{payload.filename}'")
    if not candidate.is_file():
        raise FileNotFoundError(f"No such file in sandbox: '{payload.filename}'")

    rows = []
    with candidate.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if payload.column and payload.op and payload.value is not None:
                cell = row.get(payload.column)
                if cell is None:
                    continue
                try:
                    a, b = float(cell), float(payload.value)
                except ValueError:
                    a, b = cell, payload.value  # fall back to string compare
                matched = {
                    "eq": a == b,
                    "gt": a > b,
                    "lt": a < b,
                    "gte": a >= b,
                    "lte": a <= b,
                }[payload.op]
                if not matched:
                    continue
            rows.append(row)
            if len(rows) >= payload.limit:
                break
    return rows
