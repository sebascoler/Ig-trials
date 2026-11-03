"""Servidor falso que imita graph.instagram.com y el subconjunto de PostgREST que usa ig-trials."""
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB = {"ig_videos": [], "ig_posts": [], "ig_metricas": [], "ig_resultados": []}
PK = {"ig_videos": ["slug"], "ig_posts": ["id"], "ig_metricas": ["post_id", "checkpoint_h"],
      "ig_resultados": ["video_slug"]}
IG = {"containers": {}, "media": {}, "n": 0, "calls": [], "insights": {}, "fail_next_container": False}
SEQ = {"ig_posts": 0}
LOCK = threading.Lock()


def _cmp(val, op, arg):
    if op == "eq":
        return str(val) == arg
    if op == "in":
        return str(val) in arg.strip("()").split(",")
    if val is None:
        return False
    if op in ("lte", "gte"):
        try:
            a, b = datetime.fromisoformat(str(val)), datetime.fromisoformat(arg)
        except ValueError:
            a, b = float(val), float(arg)
        return a <= b if op == "lte" else a >= b
    raise ValueError(op)


def _filtrar(filas, q):
    out = filas
    for k, vs in q.items():
        if k in ("select", "order", "limit", "on_conflict"):
            continue
        op, arg = vs[0].split(".", 1)
        out = [f for f in out if _cmp(f.get(k), op, arg)]
    if "order" in q:
        col, dir_ = q["order"][0].split(".")
        out = sorted(out, key=lambda f: (f.get(col) is None, f.get(col)), reverse=dir_ == "desc")
    if "limit" in q:
        out = out[: int(q["limit"][0])]
    if "select" in q and q["select"][0] != "*":
        cols = q["select"][0].split(",")
        out = [{c: f.get(c) for c in cols} for f in out]
    return out


def ranking():
    res = []
    principios = {p["principio"] for p in DB["ig_posts"] if p["tipo"] == "trial" and p.get("principio")}
    for pr in principios:
        posts = [p for p in DB["ig_posts"] if p.get("principio") == pr and p["tipo"] == "trial"
                 and any(r["video_slug"] == p["video_slug"] for r in DB["ig_resultados"])]
        if not posts:
            continue
        wins = sum(1 for r in DB["ig_resultados"] for p in posts if r.get("ganador_post_id") == p["id"])
        ms = [m for m in DB["ig_metricas"] for p in posts if m["post_id"] == p["id"]]
        sk = [float(m["skip_rate"]) for m in ms if m.get("skip_rate") is not None]
        wt = [m["avg_watch_ms"] for m in ms if m.get("avg_watch_ms")]
        res.append({"principio": pr, "tests": len({p["video_slug"] for p in posts}), "victorias": wins,
                    "skip_medio": round(sum(sk) / len(sk), 1) if sk else None,
                    "watch_medio_s": round(sum(wt) / len(wt) / 1000, 1) if wt else None})
    return sorted(res, key=lambda r: -r["victorias"])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def do_HEAD(self):
        ok = "/storage/v1/object/public/reels/" in self.path and "noexiste" not in self.path
        self.send_response(200 if ok else 404)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        with LOCK:
            if u.path.startswith("/rest/v1/"):
                t = u.path.split("/")[-1]
                if t == "ig_ranking_principios":
                    return self._send(200, ranking())
                if self.headers.get("apikey") != "sb_secret_test":
                    return self._send(401, {"message": "bad key"})
                return self._send(200, _filtrar(DB[t], q))
            return self._ig("GET", u.path, {k: v[0] for k, v in q.items()})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        body = self._body()
        with LOCK:
            if u.path.startswith("/rest/v1/"):
                t = u.path.split("/")[-1]
                filas = json.loads(body)
                filas = filas if isinstance(filas, list) else [filas]
                upsert = "merge-duplicates" in (self.headers.get("Prefer") or "")
                conflict = q.get("on_conflict", [",".join(PK[t])])[0].split(",")
                out = []
                for f in filas:
                    f = {k: v for k, v in f.items()}
                    ex = next((e for e in DB[t] if all(str(e.get(c)) == str(f.get(c)) for c in conflict)), None) \
                        if upsert else None
                    if ex:
                        ex.update(f)
                        out.append(ex)
                        continue
                    if t == "ig_posts":
                        SEQ["ig_posts"] += 1
                        f = {"id": SEQ["ig_posts"], "estado": "pendiente", "intentos": 0, "container_id": None,
                             "container_creado_en": None, "media_id": None, "permalink": None,
                             "publicado_en": None, "error": None, "hook": None, "principio": None, **f}
                    if t == "ig_resultados":
                        f.setdefault("decidido_en", datetime.now(timezone.utc).isoformat())
                    DB[t].append(f)
                    out.append(f)
                return self._send(201, out)
            return self._ig("POST", u.path, {k: v[0] for k, v in parse_qs(body).items()})

    def do_PATCH(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        cambios = json.loads(self._body())
        with LOCK:
            t = u.path.split("/")[-1]
            filas = _filtrar(DB[t], q)
            for f in filas:
                f.update(cambios)
            return self._send(200, filas)

    # ---------------- Instagram falso
    def _ig(self, metodo, path, p):
        IG["calls"].append((metodo, path, p))
        if p.get("access_token") == "caducado":
            return self._send(400, {"error": {"message": "Error validating access token", "code": 190}})
        partes = path.strip("/").split("/")
        if path == "/refresh_access_token":
            return self._send(200, {"access_token": "token_nuevo", "token_type": "bearer", "expires_in": 5184000})
        partes = partes[1:]  # quitar versión
        if partes == ["oauth", "access_token"]:
            if p.get("code") != "CODIGO" or p.get("client_secret") != "secreto":
                return self._send(400, {"error": {"message": "Invalid verification code"}})
            return self._send(200, {"access_token": "token_canjeado", "token_type": "bearer"})
        if partes == ["me", "assigned_instagram_accounts"]:
            if p.get("access_token") == "token_canjeado":
                return self._send(200, {"data": [{"id": "17841400000000099", "username": "sebacoler"}]})
            return self._send(200, {"data": []})  # como en la vida real con tokens del panel
        if metodo == "GET" and partes == ["17841402384432139"]:
            return self._send(200, {"id": "17841402384432139", "username": "sebacoler"})
        if partes == ["me"]:
            return self._send(200, {"user_id": "17841400000000000", "username": "sebacoler"})
        if metodo == "POST" and len(partes) == 2 and partes[1] == "media":
            if IG["fail_next_container"]:
                IG["fail_next_container"] = False
                return self._send(400, {"error": {"message": "The video_url is not reachable", "code": 9004}})
            IG["n"] += 1
            cid = f"c{IG['n']}"
            IG["containers"][cid] = {"params": p, "polls": 0,
                                     "final": "ERROR" if "malo" in p["video_url"] else "FINISHED"}
            return self._send(200, {"id": cid})
        if metodo == "POST" and len(partes) == 2 and partes[1] == "media_publish":
            c = IG["containers"][p["creation_id"]]
            if c.get("published"):
                return self._send(400, {"error": {"message": "already published"}})
            c["published"] = True
            mid = "m" + p["creation_id"][1:]
            IG["media"][mid] = c
            return self._send(200, {"id": mid})
        if metodo == "GET" and len(partes) == 1 and partes[0] in IG["containers"]:
            c = IG["containers"][partes[0]]
            c["polls"] += 1
            code = "IN_PROGRESS" if c["polls"] < 2 else c["final"]
            return self._send(200, {"status_code": code, "status": "Error: codec" if code == "ERROR" else code})
        if metodo == "GET" and len(partes) == 1 and partes[0] in IG["media"]:
            return self._send(200, {"permalink": f"https://www.instagram.com/reel/{partes[0]}/",
                                    "timestamp": "x"})
        if len(partes) == 2 and partes[1] == "insights":
            vals = IG["insights"].get(partes[0], {})
            pedidas = p["metric"].split(",")
            if "reels_skip_rate" in pedidas and len(pedidas) > 1 and IG.get("skip_unsupported_bulk"):
                return self._send(400, {"error": {"message": "metric not supported"}})
            data = [{"name": m, "period": "lifetime", "values": [{"value": vals[m]}]} for m in pedidas if m in vals]
            return self._send(200, {"data": data})
        return self._send(404, {"error": {"message": f"ruta desconocida {metodo} {path}"}})


def arrancar(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
