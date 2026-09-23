from __future__ import annotations

import json
import os
import sys
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
PLAYERS_DIR = DATA_ROOT / "data" / "players"
INDEX_DB = DATA_ROOT / "data" / "players.db"
HOST = os.environ.get("SNAKE_HOST", "0.0.0.0")
PORT = int(os.environ.get("SNAKE_PORT", "8000"))
LOBBIES: dict[str, dict] = {}
LOBBIES_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_index() -> sqlite3.Connection:
    INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(INDEX_DB)
    db.execute("CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL)")
    db.commit()
    return db


def player_defaults(player_id: str, name: str) -> dict:
    return {
        "id": player_id,
        "name": name,
        "level": 1,
        "xp": 0,
        "coins": 0,
        "best": 0,
        "totalScore": 0,
        "totalRuns": 0,
        "skin": "lime",
        "missions": [
            {"id": "eat", "label": "Съешь 10 ядер", "goal": 10, "reward": 120, "progress": 0, "done": False},
            {"id": "score", "label": "Набери 500 очков", "goal": 500, "reward": 180, "progress": 0, "done": False},
            {"id": "runs", "label": "Заверши 3 забега", "goal": 3, "reward": 90, "progress": 0, "done": False},
        ],
    }


def player_paths(player_id: str) -> tuple[Path, Path]:
    folder = PLAYERS_DIR / player_id
    return folder / "player.db", folder / "inventory.json"


def create_player(name: str) -> dict:
    name = name.strip()[:14]
    if not name:
        raise ValueError("Имя не может быть пустым")
    db = connect_index()
    existing = db.execute("SELECT id FROM players WHERE lower(name) = lower(?)", (name,)).fetchone()
    if existing:
        raise ValueError("Игрок с таким именем уже существует")
    player_id = uuid.uuid4().hex
    db.execute("INSERT INTO players VALUES (?, ?, ?)", (player_id, name, now()))
    db.commit()
    db.close()

    folder = PLAYERS_DIR / player_id
    folder.mkdir(parents=True, exist_ok=True)
    player_db, inventory_file = player_paths(player_id)
    profile = player_defaults(player_id, name)
    player_connection = sqlite3.connect(player_db)
    player_connection.execute("CREATE TABLE profile (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    player_connection.execute("INSERT INTO profile VALUES (?, ?)", ("state", json.dumps(profile, ensure_ascii=False)))
    player_connection.execute("INSERT INTO profile VALUES (?, ?)", ("created_at", now()))
    player_connection.commit()
    player_connection.close()
    inventory_file.write_text(json.dumps({"unlocked": ["lime"], "equipped": "lime"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def read_player(player_id: str) -> dict | None:
    player_db, inventory_file = player_paths(player_id)
    if not player_db.exists():
        return None
    db = sqlite3.connect(player_db)
    row = db.execute("SELECT value FROM profile WHERE key = 'state'").fetchone()
    db.close()
    if not row:
        return None
    profile = json.loads(row[0])
    if inventory_file.exists():
        inventory = json.loads(inventory_file.read_text(encoding="utf-8"))
        profile["unlocked"] = inventory.get("unlocked", ["lime"])
        profile["skin"] = inventory.get("equipped", profile.get("skin", "lime"))
    return profile


def save_player(profile: dict) -> dict:
    player_id = profile["id"]
    player_db, inventory_file = player_paths(player_id)
    if not player_db.exists():
        raise ValueError("Игрок не найден")
    inventory_file.write_text(json.dumps({"unlocked": profile.get("unlocked", ["lime"]), "equipped": profile.get("skin", "lime")}, ensure_ascii=False, indent=2), encoding="utf-8")
    db = sqlite3.connect(player_db)
    db.execute("UPDATE profile SET value = ? WHERE key = 'state'", (json.dumps(profile, ensure_ascii=False),))
    db.commit()
    db.close()
    return profile


def lobby_view(lobby: dict) -> dict:
    return {
        "code": lobby["code"],
        "hostId": lobby["hostId"],
        "started": lobby["started"],
        "allReady": bool(lobby["players"]) and all(player["ready"] for player in lobby["players"]),
        "players": lobby["players"],
    }


def get_lobby(code: str) -> dict:
    lobby = LOBBIES.get(code.upper())
    if not lobby:
        raise ValueError("Арена не найдена")
    return lobby


def create_lobby(payload: dict) -> dict:
    player_id = str(payload.get("playerId", ""))
    name = str(payload.get("name", "Игрок"))[:14].strip() or "Игрок"
    with LOBBIES_LOCK:
        code = uuid.uuid4().hex[:6].upper()
        LOBBIES[code] = {"code": code, "hostId": player_id, "started": False, "players": [{"id": player_id, "name": name, "ready": False}]}
        return lobby_view(LOBBIES[code])


def join_lobby(code: str, payload: dict) -> dict:
    player_id = str(payload.get("playerId", ""))
    name = str(payload.get("name", "Игрок"))[:14].strip() or "Игрок"
    with LOBBIES_LOCK:
        lobby = get_lobby(code)
        if lobby["started"]:
            raise ValueError("Матч уже начался")
        if not any(player["id"] == player_id for player in lobby["players"]):
            if len(lobby["players"]) >= 8:
                raise ValueError("Арена заполнена")
            lobby["players"].append({"id": player_id, "name": name, "ready": False})
        return lobby_view(lobby)


def set_ready(code: str, payload: dict) -> dict:
    with LOBBIES_LOCK:
        lobby = get_lobby(code)
        player = next((item for item in lobby["players"] if item["id"] == payload.get("playerId")), None)
        if not player:
            raise ValueError("Игрок не состоит в арене")
        player["ready"] = bool(payload.get("ready"))
        return lobby_view(lobby)


def start_lobby(code: str, payload: dict) -> dict:
    with LOBBIES_LOCK:
        lobby = get_lobby(code)
        if lobby["hostId"] != payload.get("playerId"):
            raise ValueError("Только создатель арены может начать матч")
        if not lobby["players"] or not all(player["ready"] for player in lobby["players"]):
            raise ValueError("Сначала все игроки должны нажать «Готово»")
        lobby["started"] = True
        return lobby_view(lobby)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict | list, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size) or b"{}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/lobbies/"):
            try:
                with LOBBIES_LOCK:
                    self.send_json(lobby_view(get_lobby(path.rsplit("/", 1)[-1])))
            except ValueError as error:
                self.send_json({"error": str(error)}, 404)
            return
        if path == "/api/players":
            db = connect_index()
            rows = db.execute("SELECT id, name, created_at FROM players ORDER BY created_at DESC").fetchall()
            db.close()
            self.send_json([{"id": row[0], "name": row[1], "createdAt": row[2], "profile": read_player(row[0])} for row in rows])
            return
        if path.startswith("/api/players/"):
            player = read_player(path.rsplit("/", 1)[-1])
            self.send_json(player or {"error": "Игрок не найден"}, 200 if player else 404)
            return
        if path == "/" or path == "/index.html":
            content = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/ads.mp4":
            video = ROOT / "ads.mp4"
            if video.exists():
                content = video.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/players":
                self.send_json(create_player(payload.get("name", "")), 201)
            elif path == "/api/lobbies":
                self.send_json(create_lobby(payload), 201)
            elif path.startswith("/api/lobbies/") and path.endswith("/join"):
                self.send_json(join_lobby(path.split("/")[-2], payload), 200)
            elif path.startswith("/api/lobbies/") and path.endswith("/ready"):
                self.send_json(set_ready(path.split("/")[-2], payload), 200)
            elif path.startswith("/api/lobbies/") and path.endswith("/start"):
                self.send_json(start_lobby(path.split("/")[-2], payload), 200)
            else:
                self.send_json({"error": "Маршрут не найден"}, 404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/players/"):
            self.send_json({"error": "Маршрут не найден"}, 404)
            return
        try:
            self.send_json(save_player(self.read_json()))
        except ValueError as error:
            self.send_json({"error": str(error)}, 404)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[server] {format % args}")


if __name__ == "__main__":
    connect_index().close()
    PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"NEON//SNAKE server: http://127.0.0.1:{PORT}")
    print(f"Для ngrok: ngrok http {PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
