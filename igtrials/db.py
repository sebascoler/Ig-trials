"""Cliente mínimo de Supabase (PostgREST) con requests."""
from __future__ import annotations

from typing import Any

import requests

from .config import Config


class DB:
    def __init__(self, cfg: Config):
        self.base = f"{cfg.supabase_url}/rest/v1"
        self.h = {"apikey": cfg.supabase_key, "Content-Type": "application/json"}
        # Claves antiguas (JWT) van también en Authorization; las nuevas sb_secret_ solo en apikey
        if cfg.supabase_key.startswith("eyJ"):
            self.h["Authorization"] = f"Bearer {cfg.supabase_key}"
        self.s = requests.Session()

    def _check(self, r: requests.Response) -> Any:
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    def select(self, tabla: str, **params: str) -> list[dict]:
        params.setdefault("select", "*")
        return self._check(self.s.get(f"{self.base}/{tabla}", headers=self.h, params=params, timeout=30))

    def insert(self, tabla: str, filas: list[dict] | dict) -> list[dict]:
        h = {**self.h, "Prefer": "return=representation"}
        return self._check(self.s.post(f"{self.base}/{tabla}", headers=h, json=filas, timeout=30))

    def upsert(self, tabla: str, filas: list[dict] | dict, on_conflict: str) -> list[dict]:
        h = {**self.h, "Prefer": "return=representation,resolution=merge-duplicates"}
        return self._check(
            self.s.post(f"{self.base}/{tabla}", headers=h, json=filas, params={"on_conflict": on_conflict}, timeout=30)
        )

    def update(self, tabla: str, cambios: dict, **filtros: str) -> list[dict]:
        h = {**self.h, "Prefer": "return=representation"}
        return self._check(self.s.patch(f"{self.base}/{tabla}", headers=h, json=cambios, params=filtros, timeout=30))
