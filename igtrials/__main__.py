"""Uso: python -m igtrials <comando>

  doctor            comprueba que todo está bien configurado
  diagnostico       prueba el token contra las APIs de Meta (sin mostrarlo)
  probar <archivo>  publica YA un trial reel de prueba (archivo dentro del bucket)
  programar <json>  da de alta un vídeo y sus variantes en el calendario
  estado            muestra lo próximo y lo último publicado
  publicar          (cron cada 10 min) crea contenedores y publica lo que toca
  metricas          (cron cada hora) guarda métricas a las 24h / 72h / 7d
  comparar [slug]   (cron diario) decide el hook ganador y actualiza ganchos.md
  token [--forzar]  (cron diario) renueva el token si hace >7 días
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import jobs
from .config import cargar, guardar_en_env
from .db import DB
from .ig import IG, IGError


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    cfg = cargar()
    db, ig = DB(cfg), IG(cfg)

    if cmd == "doctor":
        return doctor(cfg, db, ig)
    if cmd == "diagnostico":
        return diagnostico(cfg)
    if cmd == "publicar":
        jobs.publicar(cfg, db, ig)
    elif cmd == "metricas":
        jobs.metricas(cfg, db, ig)
    elif cmd == "comparar":
        for r in jobs.comparar(cfg, db, args[0] if args else None):
            print(f"{r['video_slug']}: {r['resumen']}")
    elif cmd == "token":
        jobs.renovar_token(cfg, ig, forzar="--forzar" in args)
    elif cmd == "programar":
        spec = json.loads(Path(args[0]).read_text())
        for f in jobs.programar(cfg, db, spec):
            print(f"{f['video_slug']} {f['tipo']} {f['variante']} → {f['publicar_en']}  ({f['archivo']})")
    elif cmd == "probar":
        spec = {"slug": "prueba", "titulo": "Prueba de conexión", "modo": "paralelo",
                "inicio": jobs.ahora().isoformat(),
                "variantes": [{"variante": "T" + jobs.ahora().strftime("%m%d%H%M"), "archivo": args[0],
                               "principio": "prueba", "caption": args[1] if len(args) > 1 else ""}]}
        jobs.programar(cfg, db, spec)
        jobs.publicar(cfg, db, ig)
        print("Si no quedó publicado aún, la próxima pasada del cron (≤10 min) lo termina. Mirá `estado`.")
    elif cmd == "estado":
        estado(cfg, db)
    else:
        print(f"Comando desconocido: {cmd}\n{__doc__}")
        return 1
    return 0


def doctor(cfg, db: DB, ig: IG) -> int:
    ok = True

    def check(nombre: str, fn):
        nonlocal ok
        try:
            print(f"  ✔ {nombre}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  ✘ {nombre}: {e}")

    print("ig-trials · diagnóstico")
    check("Variables .env", lambda: _faltan(cfg) or "completas")
    check("Supabase (tablas)", lambda: f"{len(db.select('ig_videos', select='slug'))} vídeo(s) en calendario")

    def _cuenta():
        yo = ig.yo()
        if str(yo["user_id"]) != str(cfg.ig_user_id):
            guardar_en_env("IG_USER_ID", str(yo["user_id"]))
            cfg.ig_user_id = str(yo["user_id"])
        return f"@{yo['username']} (id {yo['user_id']})"

    check("Instagram (token)", _cuenta)
    caduca = jobs._estado().get("token_caduca_en")
    if caduca:
        check("Token caduca", lambda: caduca[:10])
    return 0 if ok else 1


def diagnostico(cfg) -> int:
    """Prueba el token contra las dos APIs de Meta y dice qué modo usar. Nunca imprime el token."""
    import requests

    tok = cfg.ig_token
    print(f"Token: {len(tok)} caracteres, empieza por '{tok[:3]}…'  ·  IG_USER_ID={cfg.ig_user_id or '(vacío)'}")
    pruebas = [
        ("facebook", f"https://graph.facebook.com/{cfg.ig_api_version}/me", {"fields": "id,name"}),
        ("facebook", f"https://graph.facebook.com/{cfg.ig_api_version}/me/assigned_instagram_accounts",
         {"fields": "id,username"}),
        ("facebook", f"https://graph.facebook.com/{cfg.ig_api_version}/me/accounts",
         {"fields": "name,instagram_business_account{id,username}"}),
        ("facebook", f"https://graph.facebook.com/{cfg.ig_api_version}/me/permissions", {}),
        ("facebook", f"https://graph.facebook.com/{cfg.ig_api_version}/{cfg.ig_user_id or 'me'}",
         {"fields": "id,username"}),
        ("instagram", f"https://graph.instagram.com/{cfg.ig_api_version}/me", {"fields": "user_id,username"}),
    ]
    ok = {"facebook": False, "instagram": False}
    for host, url, params in pruebas:
        ruta = url.split(".com/")[1]
        try:
            r = requests.get(url, params={**params, "access_token": tok}, timeout=30)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"  ✘ {host:9} {ruta}: {e}")
            continue
        if "error" in d:
            print(f"  ✘ {host:9} {ruta}: {d['error'].get('message', '')[:120]}")
            continue
        txt = json.dumps(d.get("data", d), ensure_ascii=False)
        if ruta.endswith("/permissions"):
            txt = ", ".join(p["permission"] for p in d.get("data", []) if p.get("status") == "granted")
        print(f"  ✔ {host:9} {ruta}: {txt[:200]}")
        if "username" in txt and ruta.endswith((cfg.ig_user_id or "@@", "/me")) and "username" in d:
            ok[host] = True
    if ok["facebook"]:
        print("\n→ Usar IG_LOGIN=unificado (el actual por defecto).")
    elif ok["instagram"]:
        print("\n→ Usar IG_LOGIN=instagram. Ejecutá:  sed -i 's/^IG_LOGIN=.*/IG_LOGIN=instagram/' .env")
    else:
        print("\n→ Ninguna combinación llegó a la cuenta. Mandá esta salida a Claude.")
    return 0


def _faltan(cfg) -> str:
    faltan = [k for k, v in {"IG_TOKEN": cfg.ig_token, "SUPABASE_URL": cfg.supabase_url,
                             "SUPABASE_SERVICE_KEY": cfg.supabase_key}.items() if not v]
    if faltan:
        raise RuntimeError("faltan " + ", ".join(faltan))
    return ""


def estado(cfg, db: DB) -> None:
    from zoneinfo import ZoneInfo
    z = ZoneInfo(cfg.zona)

    def hora(s):
        return jobs.parse_ts(s).astimezone(z).strftime("%d/%m %H:%M") if s else "—"

    print("PRÓXIMOS")
    for p in db.select("ig_posts", estado="in.(pendiente,procesando)", order="publicar_en.asc", limit="15"):
        print(f"  {hora(p['publicar_en'])}  {p['video_slug']}/{p['variante']:<5} {p['tipo']:<6} {p['estado']}")
    print("ÚLTIMOS")
    for p in db.select("ig_posts", estado="in.(publicado,error)", order="publicar_en.desc", limit="10"):
        extra = p["permalink"] if p["estado"] == "publicado" else f"ERROR: {p['error']}"
        print(f"  {hora(p['publicado_en'] or p['publicar_en'])}  {p['video_slug']}/{p['variante']:<5} {extra}")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except IGError as e:
        print(f"Error de Instagram: {e}")
        sys.exit(2)
