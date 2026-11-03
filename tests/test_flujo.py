"""Prueba de punta a punta contra el servidor falso. Ejecutar: python -m pytest tests -q  (o python tests/test_flujo.py)"""
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_server as M  # noqa: E402

srv = M.arrancar()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
TMP = Path(tempfile.mkdtemp())
ENV = TMP / ".env"
ENV.write_text(f"IG_TOKEN=tok\nSUPABASE_URL={BASE}\nSUPABASE_SERVICE_KEY=sb_secret_test\nIG_HOST={BASE}\n"
               "IGT_MIN_REACH=300\nIGT_MARGEN_SKIP_PTS=5\nIGT_COMPARAR_A_LAS_H=72\n")
os.environ.update({"IGT_ENV_FILE": str(ENV), "IGT_SLEEP_S": "0"})

from igtrials import __main__ as cli  # noqa: E402
from igtrials import config, jobs  # noqa: E402

jobs.ESTADO_PATH = TMP / "estado.json"
jobs.GANCHOS_PATH = TMP / "ganchos.md"
config.ENV_PATH = ENV
import igtrials.config  # noqa: E402

igtrials.config.ENV_PATH = ENV


def ctx():
    cfg = config.cargar()
    from igtrials.db import DB
    from igtrials.ig import IG
    return cfg, DB(cfg), IG(cfg)


def envejecer(horas):
    """Simula que los posts se publicaron hace `horas`."""
    for p in M.DB["ig_posts"]:
        if p.get("publicado_en"):
            p["publicado_en"] = (jobs.ahora() - timedelta(hours=horas)).isoformat()


def test_todo():
    # 1. doctor rellena IG_USER_ID
    assert cli.main(["doctor"]) == 0
    assert "IG_USER_ID=17841400000000000" in ENV.read_text()

    # 2. programar 3 variantes en paralelo, empezando hace 1 minuto
    spec = {"slug": "esfuerzo", "titulo": "Qué esfuerzo usar en Claude", "modo": "paralelo",
            "inicio": (jobs.ahora() - timedelta(minutes=1)).isoformat(), "caption": "Guardalo",
            "variantes": [
                {"archivo": "esfuerzo/A.mp4", "hook": "Estás tirando tu dinero en Claude", "principio": "pérdida"},
                {"archivo": "esfuerzo/B.mp4", "hook": "3 niveles de esfuerzo", "principio": "lista"},
                {"archivo": "esfuerzo/C.mp4", "hook": "No cambies de modelo", "principio": "contraintuitivo"},
            ]}
    f = TMP / "spec.json"
    f.write_text(json.dumps(spec))
    os.environ["IGT_ESPACIADO_PARALELO_MIN"] = "0"
    assert cli.main(["programar", str(f)]) == 0
    assert len(M.DB["ig_posts"]) == 3

    # 3. publicar: la primera pasada crea contenedores (IN_PROGRESS), la segunda publica
    M.IG["fail_next_container"] = True  # error transitorio en la primera variante
    cli.main(["publicar"])
    estados = [p["estado"] for p in M.DB["ig_posts"]]
    assert estados.count("pendiente") == 1, estados  # la que falló vuelve a la cola
    cli.main(["publicar"])
    cli.main(["publicar"])
    assert all(p["estado"] == "publicado" for p in M.DB["ig_posts"]), [(p["estado"], p["error"]) for p in M.DB["ig_posts"]]
    # trial_params y share_to_feed correctos
    creates = [c for c in M.IG["calls"] if c[0] == "POST" and c[1].endswith("/media")]
    assert all(json.loads(c[2]["trial_params"]) == {"graduation_strategy": "MANUAL"} for c in creates)
    assert all(c[2]["share_to_feed"] == "false" for c in creates)
    assert M.DB["ig_posts"][0]["permalink"].startswith("https://www.instagram.com/reel/")

    # 4. métricas: definimos números; A gana en skip y watch
    numeros = {"A": (40, 9500, 900, 30), "B": (57, 7000, 850, 10), "C": (52, 8000, 700, 12)}
    for p in M.DB["ig_posts"]:
        sk, w, r, s = numeros[p["variante"]]
        M.IG["insights"][p["media_id"]] = {"views": r * 2, "reach": r, "saved": s, "likes": 5, "comments": 1,
                                           "shares": 2, "total_interactions": 8, "ig_reels_avg_watch_time": w,
                                           "ig_reels_video_view_total_time": w * r, "reels_skip_rate": sk}
    M.IG["skip_unsupported_bulk"] = True  # obliga al fallback métrica a métrica
    envejecer(25)
    cli.main(["metricas"])
    assert {m["checkpoint_h"] for m in M.DB["ig_metricas"]} == {24}
    assert all(m["skip_rate"] is not None for m in M.DB["ig_metricas"])
    cli.main(["comparar"])
    assert M.DB["ig_resultados"] == []  # aún no hay checkpoint de 72 h
    envejecer(73)
    cli.main(["metricas"])
    assert len(M.DB["ig_metricas"]) == 6

    # 5. comparar
    cli.main(["comparar"])
    r = M.DB["ig_resultados"][0]
    assert r["veredicto"] == "ganador", r
    assert r["principio_ganador"] == "pérdida"
    texto = jobs.GANCHOS_PATH.read_text()
    assert "Estás tirando tu dinero en Claude" in texto and "| pérdida |" in texto

    # 6. token
    cli.main(["token"])  # primera vez: solo empieza a contar
    assert "IG_TOKEN=tok" in ENV.read_text()
    e = json.loads(jobs.ESTADO_PATH.read_text())
    e["token_renovado_en"] = (jobs.ahora() - timedelta(days=8)).isoformat()
    jobs.ESTADO_PATH.write_text(json.dumps(e))
    cli.main(["token"])
    assert "IG_TOKEN=token_nuevo" in ENV.read_text()
    assert cli.main(["token"]) == 0 and json.loads(jobs.ESTADO_PATH.read_text())["token_renovado_en"]

    # 7. vídeo inexistente y vídeo que Instagram rechaza
    spec2 = {"slug": "malo", "titulo": "Errores", "inicio": (jobs.ahora() - timedelta(minutes=1)).isoformat(),
             "variantes": [{"archivo": "noexiste.mp4"}, {"archivo": "malo.mp4"}]}
    f.write_text(json.dumps(spec2))
    cli.main(["programar", str(f)])
    for _ in range(4):
        cli.main(["publicar"])
    malos = {p["archivo"]: p for p in M.DB["ig_posts"] if p["video_slug"] == "malo"}
    assert malos["noexiste.mp4"]["estado"] == "error" and "no está accesible" in malos["noexiste.mp4"]["error"]
    assert malos["malo.mp4"]["estado"] == "error" and "formato" in malos["malo.mp4"]["error"]
    cli.main(["estado"])


def test_decision_sin_ganador():
    cfg, _, _ = ctx()
    v = {"slug": "x"}
    trials = [{"id": 1, "variante": "A", "principio": "a", "hook": "h"},
              {"id": 2, "variante": "B", "principio": "b", "hook": "h"}]
    # margen pequeño
    med = {1: {"reach": 500, "skip_rate": 50, "avg_watch_ms": 9000, "saved": 5},
           2: {"reach": 500, "skip_rate": 52, "avg_watch_ms": 8000, "saved": 5}}
    assert jobs.decidir(v, trials, med, cfg)["veredicto"] == "sin_ganador_claro"
    # gana en skip pero pierde en watch y saves
    med = {1: {"reach": 500, "skip_rate": 40, "avg_watch_ms": 7000, "saved": 1},
           2: {"reach": 500, "skip_rate": 55, "avg_watch_ms": 9000, "saved": 9}}
    assert jobs.decidir(v, trials, med, cfg)["veredicto"] == "sin_ganador_claro"
    # poco alcance
    med = {1: {"reach": 100, "skip_rate": 30, "avg_watch_ms": 9000, "saved": 5},
           2: {"reach": 500, "skip_rate": 55, "avg_watch_ms": 8000, "saved": 5}}
    assert jobs.decidir(v, trials, med, cfg)["veredicto"] == "datos_insuficientes"
    # skip en fracción (0-1) se normaliza
    assert jobs._pct(0.42) == 42.0 and jobs._pct(57) == 57.0


if __name__ == "__main__":
    test_todo()
    test_decision_sin_ganador()
    print("\nOK: todas las pruebas pasaron")
