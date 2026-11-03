"""Cliente de la API de Instagram.

Dos modos de login (IG_LOGIN en .env):
- "unificado" (por defecto): apps de Meta creadas desde finales de oct-2026. Token de usuario del sistema
  (SUAT, EAA...) y graph.facebook.com.
- "instagram": apps antiguas con "API setup with Instagram login". Token de 60 días y graph.instagram.com.
Publicar, consultar el estado del contenedor y leer métricas usan las mismas rutas en los dos modos.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from .config import Config

METRICAS_REELS = [
    "views",
    "reach",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
    "reels_skip_rate",
]


class IGError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, data: Any = None):
        super().__init__(msg)
        self.code = code
        self.data = data


class IG:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = requests.Session()

    def _req(self, metodo: str, ruta: str, base: str | None = None, **params: Any) -> dict:
        params["access_token"] = self.cfg.ig_token
        url = f"{base or self.cfg.ig_base}/{ruta.lstrip('/')}"
        if metodo == "GET":
            r = self.s.get(url, params=params, timeout=60)
        else:
            r = self.s.post(url, data=params, timeout=60)
        try:
            data = r.json()
        except ValueError:
            raise IGError(f"Respuesta no JSON ({r.status_code}): {r.text[:200]}")
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
            raise IGError(f"Instagram {r.status_code}: {msg}", err.get("code"), data)
        return data

    # --- cuenta y token -------------------------------------------------
    def yo(self) -> dict:
        """Devuelve {"user_id", "username"} de la cuenta de Instagram del token."""
        if self.cfg.ig_login == "instagram":
            return self._req("GET", "me", fields="user_id,username")
        # 1) Si conocemos el ID de la cuenta, preguntamos directamente por ella (lo más fiable con tokens del panel)
        if self.cfg.ig_user_id:
            try:
                d = self._req("GET", self.cfg.ig_user_id, fields="id,username")
                return {"user_id": d["id"], "username": d.get("username", "?")}
            except IGError:
                pass
        # 2) Si no, la lista de cuentas que Meta asignó al usuario del sistema
        cuentas = self._req("GET", "me/assigned_instagram_accounts", fields="id,username").get("data", [])
        if not cuentas:
            raise IGError("El token no tiene ninguna cuenta de Instagram asignada. Generalo de nuevo "
                          "eligiendo @tu-cuenta en el login.")
        elegida = next((c for c in cuentas if str(c["id"]) == str(self.cfg.ig_user_id)), cuentas[0])
        return {"user_id": elegida["id"], "username": elegida.get("username", "?")}

    def refrescar_token(self) -> dict:
        """Solo para el modo "instagram" (token de 60 días)."""
        # Este endpoint no lleva versión
        return self._req("GET", "refresh_access_token", base=self.cfg.ig_host, grant_type="ig_refresh_token")

    # --- publicación ----------------------------------------------------
    def crear_contenedor(self, video_url: str, caption: str, trial: bool) -> str:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "false" if trial else "true",
        }
        if trial:
            params["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
        return self._req("POST", f"{self.cfg.ig_user_id}/media", **params)["id"]

    def estado_contenedor(self, container_id: str) -> dict:
        return self._req("GET", container_id, fields="status_code,status")

    def publicar(self, container_id: str) -> str:
        return self._req("POST", f"{self.cfg.ig_user_id}/media_publish", creation_id=container_id)["id"]

    def info_media(self, media_id: str) -> dict:
        return self._req("GET", media_id, fields="permalink,timestamp")

    # --- métricas -------------------------------------------------------
    def insights(self, media_id: str) -> dict[str, Any]:
        """Pide todas las métricas; si alguna no está soportada, las pide una a una y salta las que fallen."""
        try:
            data = self._req("GET", f"{media_id}/insights", metric=",".join(METRICAS_REELS))
            return _aplanar(data)
        except IGError:
            res: dict[str, Any] = {}
            for m in METRICAS_REELS:
                try:
                    res.update(_aplanar(self._req("GET", f"{media_id}/insights", metric=m)))
                except IGError:
                    continue
            return res


def _aplanar(data: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in data.get("data", []):
        vals = item.get("values") or []
        if vals:
            out[item["name"]] = vals[0].get("value")
        elif "total_value" in item:
            out[item["name"]] = item["total_value"].get("value")
    return out
