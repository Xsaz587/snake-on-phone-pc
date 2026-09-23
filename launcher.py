from __future__ import annotations

import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

import server


def main() -> None:
    server.connect_index().close()
    server.PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    http_server = ThreadingHTTPServer(("127.0.0.1", server.PORT), server.Handler)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    time.sleep(0.35)
    webbrowser.open(f"http://127.0.0.1:{server.PORT}/")
    print(f"NEON//SNAKE запущен: http://127.0.0.1:{server.PORT}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        http_server.shutdown()


if __name__ == "__main__":
    main()