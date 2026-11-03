"""Lee la configuración del archivo .env (sin dependencias externas)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.environ.get("IGT_ENV_FILE", RAIZ / ".env"))


def leer_env(path: Path = ENV_PATH) -> dict[str, str]:
    datos: dict[str, str] = {}
    if path.exists():
        for linea in path.read_text().splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def guardar_en_env(clave: str, valor: str, path: Path = ENV_PATH) -> None:
    """Actualiza (o añade) una clave en .env de forma atómica."""
    lineas = path.read_text().splitlines() if path.exists() else []
    hecho = False
    for i, linea in enumerate(lineas):
        if linea.strip().startswith(f"{clave}="):
            lineas[i] = f"{clave}={valor}"
            hecho = True
    if not hecho:
        lineas.append(f"{clave}={valor}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lineas) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


@dataclass
class Config:
    ig_token: str
    ig_user_id: str
    ig_login: str  # "unificado" (apps nuevas, graph.facebook.com) o "instagram" (apps antiguas, graph.instagram.com)
    ig_api_version: str
    ig_host: str
    supabase_url: str
    supabase_key: str
    bucket: str
    zona: str
    min_reach: int
    margen_skip_pts: float
    comparar_a_las_h: int
    espaciado_paralelo_min: int
    max_trials_24h: int
    ntfy_topic: str

    @property
    def ig_base(self) -> str:
        return f"{self.ig_host}/{self.ig_api_version}"

    def url_publica(self, archivo: str) -> str:
        return f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{archivo.lstrip('/')}"


def cargar() -> Config:
    e = {**leer_env(), **{k: v for k, v in os.environ.items() if k.startswith(("IG_", "SUPABASE_", "IGT_"))}}
    login = e.get("IG_LOGIN", "unificado").lower()
    host_defecto = "https://graph.instagram.com" if login == "instagram" else "https://graph.facebook.com"
    return Config(
        ig_token=e.get("IG_TOKEN", ""),
        ig_user_id=e.get("IG_USER_ID", ""),
        ig_login=login,
        ig_api_version=e.get("IG_API_VERSION", "v26.0"),
        ig_host=e.get("IG_HOST", host_defecto).rstrip("/"),
        supabase_url=e.get("SUPABASE_URL", "").rstrip("/"),
        supabase_key=e.get("SUPABASE_SERVICE_KEY", ""),
        bucket=e.get("IGT_BUCKET", "reels"),
        zona=e.get("IGT_ZONA", "Europe/Madrid"),
        min_reach=int(e.get("IGT_MIN_REACH", "300")),
        margen_skip_pts=float(e.get("IGT_MARGEN_SKIP_PTS", "5")),
        comparar_a_las_h=int(e.get("IGT_COMPARAR_A_LAS_H", "72")),
        espaciado_paralelo_min=int(e.get("IGT_ESPACIADO_PARALELO_MIN", "60")),
        max_trials_24h=int(e.get("IGT_MAX_TRIALS_24H", "15")),
        ntfy_topic=e.get("IGT_NTFY_TOPIC", ""),
    )
