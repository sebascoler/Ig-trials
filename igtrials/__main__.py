"""Uso: python -m igtrials <comando>

  doctor            comprueba que todo está bien configurado
  diagnostico       prueba el token contra las APIs de Meta (sin mostrarlo)
  buscar            reintenta encontrar la cuenta con el token guardado
  canjear           convierte el ?code= del login unificado en token (pide el App Secret oculto)
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
    if cmd == "canjear":
        return canjear(cfg)
    if cmd == "buscar":  # reintenta encontrar la cuenta con el token ya guardado, sin repetir el login
        import getpass
        from .config import leer_env
        env = leer_env()
        app_id = env.get("META_APP_ID") or input("App ID de Meta: ").strip()
        secreto = getpass.getpass("App Secret (no se ve al pegar): ").strip()
        c = buscar_cuenta(cfg.ig_base, cfg.ig_token, app_id, secreto, cfg.ig_user_id)
        if c:
            guardar_en_env("IG_USER_ID", str(c["id"]))
            print(f"✔ Cuenta encontrada: @{c['username']} (id {c['id']})")
            return 0
        print("✘ Sin cuenta. Mandá esta salida a Claude.")
        return 1
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


def canjear(cfg) -> int:
    """Convierte el ?code=... del login unificado en un token de usuario del sistema con la cuenta asignada.
    Pide el App Secret sin mostrarlo y guarda token e ID en .env. Nada se imprime."""
    import getpass
    from urllib.parse import parse_qs, urlparse

    import requests

    from .config import leer_env

    env = leer_env()
    base = cfg.ig_base if cfg.ig_login != "instagram" else f"https://graph.facebook.com/{cfg.ig_api_version}"
    app_id = env.get("META_APP_ID") or input("App ID de Meta (número largo): ").strip()
    secreto = getpass.getpass("App Secret (no se ve al pegar): ").strip()
    url = getpass.getpass("URL completa a la que te llevó el login (no se ve al pegar): ").strip()
    q = parse_qs(urlparse(url).query)
    code = (q.get("code") or [url if "://" not in url else ""])[0]
    if not code:
        print("✘ Esa URL no tiene ?code=. Repetí el login y copiá la dirección completa de la barra del navegador.")
        return 1
    redirect = env.get("META_REDIRECT_URI", "https://aprendes.ai/")
    params = {"client_id": app_id, "client_secret": secreto, "code": code}
    d = requests.get(f"{base}/oauth/access_token", params={**params, "redirect_uri": redirect}, timeout=30).json()
    if "error" in d:
        d2 = requests.get(f"{base}/oauth/access_token", params=params, timeout=30).json()
        d = d2 if "error" not in d2 else d
    if "error" in d:
        print(f"✘ Meta rechazó el canje: {d['error'].get('message', '')[:200]}")
        print("  (El code caduca en minutos y solo sirve una vez: repetí el login y ejecutá esto enseguida.)")
        return 1
    token = d["access_token"]
    guardar_en_env("IG_TOKEN", token)       # lo guardamos siempre: así se puede seguir diagnosticando sin repetir login
    guardar_en_env("META_APP_ID", app_id)
    print(f"✔ Token nuevo guardado en .env (caducidad: "
          f"{'nunca' if not d.get('expires_in') else str(int(d['expires_in']) // 86400) + ' días'}).")
    cuenta = buscar_cuenta(base, token, app_id, secreto, cfg.ig_user_id)
    if not cuenta:
        print("✘ El token no llega a ninguna cuenta de Instagram. Mandá esta salida a Claude.")
        return 1
    guardar_en_env("IG_USER_ID", str(cuenta["id"]))
    print(f"✔ Cuenta encontrada: @{cuenta.get('username')} (id {cuenta['id']}). Ya podés ejecutar: bash install.sh")
    return 0


def buscar_cuenta(base: str, token: str, app_id: str, secreto: str, id_conocido: str) -> dict | None:
    """Prueba todas las vías conocidas para llegar a la cuenta de Instagram. Imprime solo IDs y nombres."""
    import requests

    def get(ruta, **params):
        try:
            return requests.get(f"{base}/{ruta}", params={**params, "access_token": token}, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            return {"error": {"message": str(e)}}

    candidatos: list[str] = []
    # 1. Qué cuentas concretas cubre cada permiso (debug_token con el token de app)
    dbg = requests.get(f"{base}/debug_token", params={"input_token": token, "access_token": f"{app_id}|{secreto}"},
                       timeout=30).json().get("data", {})
    print(f"  · tipo de token: {dbg.get('type')} · válido: {dbg.get('is_valid')} · usuario: {dbg.get('user_id')}")
    for g in dbg.get("granular_scopes", []):
        print(f"  · {g.get('scope')}: {g.get('target_ids') or 'sin cuentas concretas'}")
        if g.get("scope", "").startswith("instagram"):
            candidatos += [str(t) for t in g.get("target_ids") or []]
    # 2. Cuentas asignadas al usuario del sistema
    asignadas = get("me/assigned_instagram_accounts", fields="id,username")
    print(f"  · assigned_instagram_accounts: {asignadas.get('data', asignadas.get('error', {}).get('message'))}")
    candidatos += [str(c["id"]) for c in asignadas.get("data", [])]
    # 3. Cuentas del portfolio del cliente
    yo = get("me", fields="id,name,client_business_id")
    biz = yo.get("client_business_id")
    print(f"  · usuario del sistema: {yo.get('name')} · portfolio: {biz}")
    if biz:
        for ruta in ("owned_instagram_accounts", "instagram_accounts"):
            r = get(f"{biz}/{ruta}", fields="id,username")
            print(f"  · {ruta}: {r.get('data', r.get('error', {}).get('message', ''))}")
            candidatos += [str(c["id"]) for c in r.get("data", [])]
    if id_conocido:
        candidatos.append(str(id_conocido))
    # Probar cada candidato directamente
    vistos = set()
    for cid in candidatos:
        if cid in vistos:
            continue
        vistos.add(cid)
        r = get(cid, fields="id,username")
        if r.get("username"):
            return {"id": r["id"], "username": r["username"]}
        print(f"  · {cid}: {r.get('error', {}).get('message', 'sin username')[:100]}")
    return None


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
