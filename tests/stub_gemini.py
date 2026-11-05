"""A stand-in for the Gemini API, so model handover and failures can be tested without a key or any quota."""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.stub_anthropic import Handler as AnthropicHandler


class Handler(BaseHTTPRequestHandler):
    behaviour: dict = {}  # model -> busy, full, bad_key, slow or garbage. Anything else answers normally.
    requests: list = []

    def log_message(self, *_):
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code, status):
        self._reply({"error": {"code": code, "message": f"stub {status}", "status": status}}, code)

    def _answer(self, text):
        self._reply({"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                     "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 150}})

    def do_POST(self):
        model = self.path.split("/models/")[1].split(":")[0]
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Handler.requests.append({"model": model, "headers": dict(self.headers), "body": request})
        behaviour = Handler.behaviour.get(model)
        if behaviour == "busy":
            return self._fail(503, "UNAVAILABLE")
        if behaviour == "full":
            return self._fail(429, "RESOURCE_EXHAUSTED")
        if behaviour == "bad_key":
            return self._fail(400, "INVALID_ARGUMENT")
        if behaviour == "garbage":
            return self._answer("this is not the requested json")
        if behaviour == "slow":
            time.sleep(3)
        first = request["contents"][0]["parts"][0]
        if "inlineData" not in first:
            return self._answer("Good news: your details and documents look right. We just need a little more to finish.")
        encoded = first["inlineData"]["data"]  # the Google library sends URL-safe base64
        data = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        self._answer(json.dumps(AnthropicHandler._read({"source": {"data": base64.b64encode(data).decode()}})))


def serve(behaviour=None):
    Handler.behaviour = behaviour or {}
    Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
