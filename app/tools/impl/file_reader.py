"""
File reader tool. Enforces a hard sandbox jail: resolves the requested path
and verifies it is a descendant of SANDBOX_DIR. This blocks path traversal
(../../etc/passwd) even if the LLM tries to construct a clever relative path,
because we compare *resolved absolute paths*, not raw strings.
"""
from pathlib import Path
from pydantic import BaseModel, field_validator

SANDBOX_DIR = (Path(__file__).resolve().parents[3] / "sandbox_files").resolve()
MAX_BYTES = 10_000


class FileReaderInput(BaseModel):
    filename: str

    @field_validator("filename")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("filename must not be empty")
        return v


def run(args: dict) -> str:
    payload = FileReaderInput(**args)
    candidate = (SANDBOX_DIR / payload.filename).resolve()

    # The jail check: candidate must be SANDBOX_DIR itself or a descendant of it.
    if SANDBOX_DIR not in candidate.parents and candidate != SANDBOX_DIR:
        raise PermissionError(
            f"Path escapes sandbox: '{payload.filename}' resolved to "
            f"'{candidate}', which is outside '{SANDBOX_DIR}'."
        )

    if not candidate.is_file():
        raise FileNotFoundError(f"No such file in sandbox: '{payload.filename}'")

    data = candidate.read_bytes()[:MAX_BYTES]
    return data.decode("utf-8", errors="replace")
