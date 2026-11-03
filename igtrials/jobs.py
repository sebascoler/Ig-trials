"""Las tareas que corre el cron: publicar, medir, comparar, renovar token."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .config import RAIZ, Config, guardar_en_env
from .db import DB
from .ig import IG, IGError

CHECKPOINTS_H = [24, 72, 168]
ESTADO_PATH = RAIZ / "estado.json"
GANCHOS_PATH = RAIZ / "ganchos.md"


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def log(msg: str) -> None:
    print(f"[{ahora().strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)


def avisar(cfg: Config, titulo: str, msg: str, prioridad: str = "default") -> None:
    """Push al móvil vía ntfy.sh (opcional, si IGT_NTFY_TOPIC está configurado)."""
    log(f"{titulo}: {msg}")
    if not cfg.ntfy_topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{cfg.ntfy_topic}",
            data=msg.encode(),
            headers={"Title": titulo.encode("utf-8").decode("latin-1", "ignore"), "Priority": prioridad},
            timeout=10,
        )
    except requests.RequestException:
        pass


# --------------------------------------------------------------------------- publicar
def publicar(cfg: Config, db: DB, ig: IG) -> None:
    """Paso 1: crea contenedores de lo que toca. Paso 2: publica los que Instagram ya procesó.
    No bloquea: cada pasada del cron avanza lo que pueda."""
    _avanzar_contenedores(cfg, db, ig)

    pendientes = db.select(
        "ig_posts",
        estado="eq.pendiente",
        publicar_en=f"lte.{ahora().isoformat()}",
        order="publicar_en.asc",
        limit="5",
    )
    if not pendientes:
        return

    hace_24h = (ahora() - timedelta(hours=24)).isoformat()
    recientes = db.select("ig_posts", select="id", tipo="eq.trial", publicado_en=f"gte.{hace_24h}")
    cupo = cfg.max_trials_24h - len(recientes)

    for p in pendientes:
        if p["tipo"] == "trial":
            if cupo <= 0:
                log(f"Cupo diario de trials alcanzado ({cfg.max_trials_24h}); {p['video_slug']}/{p['variante']} espera")
                continue
            cupo -= 1
        url = cfg.url_publica(p["archivo"])
        try:
            h = requests.head(url, timeout=20, allow_redirects=True)
            if h.status_code != 200:
                raise IGError(f"El vídeo no está accesible en {url} (HTTP {h.status_code}). ¿Lo subiste al bucket?")
            cid = ig.crear_contenedor(url, p.get("caption") or "", trial=p["tipo"] == "trial")
            db.update(
                "ig_posts",
                {"estado": "procesando", "container_id": cid, "container_creado_en": ahora().isoformat(),
                 "error": None},
                id=f"eq.{p['id']}",
            )
            log(f"Contenedor creado {cid} para {p['video_slug']}/{p['variante']}")
        except (IGError, requests.RequestException) as e:
            _fallo(cfg, db, p, str(e))

    # Dar a Instagram un respiro y volver a mirar en la misma pasada
    time.sleep(int(os.environ.get("IGT_SLEEP_S", "45")))
    _avanzar_contenedores(cfg, db, ig)


def _avanzar_contenedores(cfg: Config, db: DB, ig: IG) -> None:
    for p in db.select("ig_posts", estado="eq.procesando", order="publicar_en.asc"):
        try:
            st = ig.estado_contenedor(p["container_id"])
            code = st.get("status_code")
            if code == "FINISHED":
                mid = ig.publicar(p["container_id"])
                try:
                    link = ig.info_media(mid).get("permalink")
                except IGError:
                    link = None
                db.update(
                    "ig_posts",
                    {"estado": "publicado", "media_id": mid, "permalink": link, "publicado_en": ahora().isoformat()},
                    id=f"eq.{p['id']}",
                )
                tipo = "Trial" if p["tipo"] == "trial" else "Reel"
                avisar(cfg, f"{tipo} publicado", f"{p['video_slug']} · {p['variante']} · {link or mid}")
            elif code == "ERROR":
                _fallo(cfg, db, p, f"Instagram no pudo procesar el vídeo: {st.get('status')}. "
                                   "Revisá formato (MP4 H.264 + AAC, 9:16, 3 s-15 min, <300 MB).", final=True)
            elif code == "EXPIRED":
                db.update("ig_posts", {"estado": "pendiente", "container_id": None, "container_creado_en": None},
                          id=f"eq.{p['id']}")
                log(f"Contenedor caducado; se recrea: {p['video_slug']}/{p['variante']}")
            else:  # IN_PROGRESS
                creado = parse_ts(p.get("container_creado_en"))
                if creado and ahora() - creado > timedelta(hours=3):
                    _fallo(cfg, db, p, "Instagram lleva más de 3 h procesando el vídeo", final=True)
        except IGError as e:
            _fallo(cfg, db, p, str(e))


def _fallo(cfg: Config, db: DB, p: dict, msg: str, final: bool = False) -> None:
    intentos = p.get("intentos", 0) + 1
    # Errores transitorios: vuelve a la cola y reintenta en la siguiente pasada, hasta 3 veces
    if not final and intentos < 3:
        db.update("ig_posts", {"estado": "pendiente", "container_id": None, "container_creado_en": None,
                               "error": msg, "intentos": intentos}, id=f"eq.{p['id']}")
        log(f"Fallo (intento {intentos}/3) {p['video_slug']}/{p['variante']}: {msg}")
        return
    db.update("ig_posts", {"estado": "error", "error": msg, "intentos": intentos}, id=f"eq.{p['id']}")
    avisar(cfg, "Error publicando", f"{p['video_slug']}/{p['variante']}: {msg}", "high")


# --------------------------------------------------------------------------- métricas
def metricas(cfg: Config, db: DB, ig: IG) -> None:
    """Saca una foto de métricas de cada post a las 24 h, 72 h y 7 días de publicarse."""
    desde = (ahora() - timedelta(days=10)).isoformat()
    posts = db.select("ig_posts", estado="eq.publicado", publicado_en=f"gte.{desde}")
    if not posts:
        return
    ids = ",".join(str(p["id"]) for p in posts)
    hechas = {(m["post_id"], m["checkpoint_h"])
              for m in db.select("ig_metricas", select="post_id,checkpoint_h", post_id=f"in.({ids})")}
    for p in posts:
        edad_h = (ahora() - parse_ts(p["publicado_en"])).total_seconds() / 3600
        for cp in CHECKPOINTS_H:
            if edad_h < cp or (p["id"], cp) in hechas:
                continue
            # Si se nos pasó un checkpoint por más de 12 h (VPS caído), no lo inventamos
            if edad_h > cp + 12 and cp != CHECKPOINTS_H[-1]:
                continue
            try:
                m = ig.insights(p["media_id"])
            except IGError as e:
                log(f"No pude leer métricas de {p['video_slug']}/{p['variante']}: {e}")
                break
            fila = {
                "post_id": p["id"],
                "checkpoint_h": cp,
                "views": m.get("views"),
                "reach": m.get("reach"),
                "likes": m.get("likes"),
                "comments": m.get("comments"),
                "saved": m.get("saved"),
                "shares": m.get("shares"),
                "total_interactions": m.get("total_interactions"),
                "avg_watch_ms": m.get("ig_reels_avg_watch_time"),
                "total_watch_ms": m.get("ig_reels_video_view_total_time"),
                "skip_rate": _pct(m.get("reels_skip_rate")),
                "raw": m,
            }
            db.upsert("ig_metricas", fila, on_conflict="post_id,checkpoint_h")
            log(f"Métricas {cp}h {p['video_slug']}/{p['variante']}: skip={fila['skip_rate']} "
                f"watch={(fila['avg_watch_ms'] or 0) / 1000:.1f}s reach={fila['reach']}")


def _pct(v) -> float | None:
    if v is None:
        return None
    v = float(v)
    return round(v * 100, 2) if v <= 1 else round(v, 2)


# --------------------------------------------------------------------------- comparar
def comparar(cfg: Config, db: DB, forzar_slug: str | None = None) -> list[dict]:
    """Para cada vídeo con todas sus variantes medidas al checkpoint, decide si hay ganador.
    Regla: gana quien tiene el menor skip rate con ventaja >= margen Y además lidera otra señal
    (watch medio o guardados/alcance). Solo cuentan variantes con alcance >= mínimo."""
    cp = cfg.comparar_a_las_h
    resueltos = {r["video_slug"] for r in db.select("ig_resultados", select="video_slug")}
    videos = db.select("ig_videos") if not forzar_slug else db.select("ig_videos", slug=f"eq.{forzar_slug}")
    nuevos = []
    for v in videos:
        if v["slug"] in resueltos and not forzar_slug:
            continue
        trials = db.select("ig_posts", video_slug=f"eq.{v['slug']}", tipo="eq.trial", order="variante.asc")
        trials = [t for t in trials if t["estado"] != "cancelado"]
        if len(trials) < 2 or any(t["estado"] in ("pendiente", "procesando", "error") for t in trials):
            continue
        publicados = [t for t in trials if t["estado"] == "publicado"]
        ids = ",".join(str(t["id"]) for t in publicados) or "0"
        med = {m["post_id"]: m for m in db.select("ig_metricas", post_id=f"in.({ids})", checkpoint_h=f"eq.{cp}")}
        if len(med) < len(publicados):
            continue  # aún falta el checkpoint de alguna variante
        res = decidir(v, publicados, med, cfg)
        db.upsert("ig_resultados", res, on_conflict="video_slug")
        nuevos.append(res)
        avisar(cfg, f"Resultado: {v['titulo']}", res["resumen"])
    if nuevos or forzar_slug:
        escribir_ganchos(db)
    return nuevos


def decidir(video: dict, trials: list[dict], med: dict[int, dict], cfg: Config) -> dict:
    filas = []
    for t in trials:
        m = med[t["id"]]
        reach = m.get("reach") or 0
        filas.append({
            "post_id": t["id"],
            "variante": t["variante"],
            "principio": t.get("principio"),
            "hook": t.get("hook"),
            "reach": reach,
            "skip": float(m["skip_rate"]) if m.get("skip_rate") is not None else None,
            "watch_s": round((m.get("avg_watch_ms") or 0) / 1000, 1),
            "saves_pct": round(100 * (m.get("saved") or 0) / reach, 2) if reach else 0,
            "valida": reach >= cfg.min_reach and m.get("skip_rate") is not None,
        })
    base = {"video_slug": video["slug"], "checkpoint_h": cfg.comparar_a_las_h, "tabla": filas,
            "ganador_post_id": None, "principio_ganador": None, "hook_ganador": None}
    validas = [f for f in filas if f["valida"]]
    if len(validas) < 2:
        return {**base, "veredicto": "datos_insuficientes",
                "resumen": f"Solo {len(validas)} variante(s) llegaron a {cfg.min_reach} de alcance a las "
                           f"{cfg.comparar_a_las_h} h. No hay muestra para decidir."}

    por_skip = sorted(validas, key=lambda f: f["skip"])
    mejor, segundo = por_skip[0], por_skip[1]
    margen = segundo["skip"] - mejor["skip"]
    lidera_watch = mejor["watch_s"] >= max(f["watch_s"] for f in validas)
    lidera_saves = mejor["saves_pct"] >= max(f["saves_pct"] for f in validas) and mejor["saves_pct"] > 0
    otra_senal = lidera_watch or lidera_saves

    detalle = " · ".join(f"{f['variante']}: skip {f['skip']:.0f}%, {f['watch_s']}s" for f in por_skip)
    if margen >= cfg.margen_skip_pts and otra_senal:
        senal = "watch medio" if lidera_watch else "guardados"
        return {**base, "veredicto": "ganador", "ganador_post_id": mejor["post_id"],
                "principio_ganador": mejor["principio"], "hook_ganador": mejor["hook"],
                "resumen": f"Gana {mejor['variante']} ({mejor['principio'] or 'sin principio'}): "
                           f"{margen:.0f} pts menos de skip que la siguiente y también lidera en {senal}. {detalle}"}
    motivo = (f"la ventaja en skip es de solo {margen:.1f} pts (mínimo {cfg.margen_skip_pts:g})"
              if margen < cfg.margen_skip_pts else
              f"{mejor['variante']} gana en skip pero no lidera ni en watch medio ni en guardados")
    return {**base, "veredicto": "sin_ganador_claro", "resumen": f"Sin ganador claro: {motivo}. {detalle}"}


def escribir_ganchos(db: DB) -> Path:
    resultados = db.select("ig_resultados", order="decidido_en.desc")
    videos = {v["slug"]: v for v in db.select("ig_videos")}
    ranking = db.select("ig_ranking_principios")
    out = ["# Ganchos: qué aprendimos", "",
           "_Archivo generado automáticamente por ig-trials. Usalo como contexto al escribir el próximo guion._", ""]
    if ranking:
        out += ["## Ranking de principios", "", "| Principio | Tests | Victorias | Skip medio | Watch medio |",
                "|---|---|---|---|---|"]
        out += [f"| {r['principio']} | {r['tests']} | {r['victorias']} | {r['skip_medio']}% | {r['watch_medio_s']}s |"
                for r in ranking]
        out.append("")
    out += ["## Resultados por vídeo", ""]
    for r in resultados:
        titulo = videos.get(r["video_slug"], {}).get("titulo", r["video_slug"])
        fecha = r["decidido_en"][:10]
        out.append(f"### {titulo} ({fecha}) — {r['veredicto'].replace('_', ' ')}")
        if r.get("hook_ganador"):
            out.append(f"- Hook ganador: «{r['hook_ganador']}» ({r.get('principio_ganador') or 's/p'})")
        out.append(f"- {r['resumen']}")
        out.append("")
    GANCHOS_PATH.write_text("\n".join(out))
    return GANCHOS_PATH


# --------------------------------------------------------------------------- token
def _estado() -> dict:
    return json.loads(ESTADO_PATH.read_text()) if ESTADO_PATH.exists() else {}


def _guardar_estado(e: dict) -> None:
    ESTADO_PATH.write_text(json.dumps(e, indent=2))


def renovar_token(cfg: Config, ig: IG, cada_dias: int = 7, forzar: bool = False) -> bool:
    """Modo "unificado": el token de usuario del sistema no se renueva; solo comprobamos cada día que siga
    vivo y avisamos al móvil si deja de funcionar.
    Modo "instagram": el token dura 60 días; lo renovamos cada semana para tener margen de sobra."""
    if cfg.ig_login != "instagram":
        try:
            yo = ig.yo()
            e = _estado()
            e["token_comprobado_en"] = ahora().isoformat()
            _guardar_estado(e)
            log(f"Token OK (@{yo['username']})")
        except IGError as err:
            avisar(cfg, "Token de Instagram", f"El token dejó de funcionar: {err}. Generá uno nuevo en Meta "
                                              "(Casos de uso → Instagram → Generar token) y pegalo en .env.", "high")
        return False
    e = _estado()
    ultimo = parse_ts(e.get("token_renovado_en"))
    if not forzar and ultimo is None:
        # Primera vez: el token es recién generado (Meta no deja renovarlo antes de 24 h). Empezamos a contar.
        e["token_renovado_en"] = ahora().isoformat()
        _guardar_estado(e)
        return False
    if not forzar and ahora() - ultimo < timedelta(days=cada_dias):
        return False
    try:
        r = ig.refrescar_token()
    except IGError as err:
        avisar(cfg, "Token de Instagram", f"No se pudo renovar: {err}. Si caducó, generá uno nuevo en Meta.", "high")
        return False
    guardar_en_env("IG_TOKEN", r["access_token"])
    cfg.ig_token = r["access_token"]
    dias = int(r.get("expires_in", 0)) // 86400
    e["token_renovado_en"] = ahora().isoformat()
    e["token_caduca_en"] = (ahora() + timedelta(seconds=int(r.get("expires_in", 0)))).isoformat()
    _guardar_estado(e)
    log(f"Token renovado, válido {dias} días más")
    return True


# --------------------------------------------------------------------------- programar
def programar(cfg: Config, db: DB, spec: dict) -> list[dict]:
    """Da de alta un vídeo y sus variantes en el calendario.
    paralelo: todas el mismo día, separadas N minutos (misma franja → sin efecto día/hora).
    secuencial: una por día, a la misma hora."""
    zona = ZoneInfo(cfg.zona)
    inicio = datetime.fromisoformat(spec["inicio"])
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=zona)
    modo = spec.get("modo", "paralelo")
    db.upsert("ig_videos", {"slug": spec["slug"], "titulo": spec["titulo"], "modo": modo,
                            "notas": spec.get("notas")}, on_conflict="slug")
    filas = []
    for i, var in enumerate(spec["variantes"]):
        cuando = (inicio + timedelta(minutes=i * cfg.espaciado_paralelo_min) if modo == "paralelo"
                  else inicio + timedelta(days=i))
        filas.append({
            "video_slug": spec["slug"], "tipo": "trial",
            "variante": var.get("variante") or chr(ord("A") + i),
            "hook": var.get("hook"), "principio": var.get("principio"),
            "archivo": var["archivo"], "caption": var.get("caption", spec.get("caption", "")),
            "publicar_en": cuando.astimezone(timezone.utc).isoformat(),
        })
    if spec.get("normal"):
        n = spec["normal"]
        cuando = datetime.fromisoformat(n["publicar_en"])
        if cuando.tzinfo is None:
            cuando = cuando.replace(tzinfo=zona)
        filas.append({"video_slug": spec["slug"], "tipo": "normal", "variante": "FEED",
                      "archivo": n["archivo"], "caption": n.get("caption", spec.get("caption", "")),
                      "publicar_en": cuando.astimezone(timezone.utc).isoformat()})
    return db.upsert("ig_posts", filas, on_conflict="video_slug,tipo,variante")
