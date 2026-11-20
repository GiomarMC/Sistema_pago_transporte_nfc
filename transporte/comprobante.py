"""Abre una sola vez un comprobante en memoria en el navegador de ventanilla."""
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def abrir_comprobante(html, espera_segundos=30):
    ruta = "/" + secrets.token_urlsafe(18)
    servido = threading.Event()

    class Vista(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != ruta or servido.is_set():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(html)
            servido.set()

        def log_message(self, *_):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Vista)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        url = f"http://127.0.0.1:{servidor.server_port}{ruta}"
        if not webbrowser.open(url):
            print(f"  Abre este comprobante local durante los próximos {espera_segundos} s: {url}")
        return servido.wait(espera_segundos)
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(timeout=2)
