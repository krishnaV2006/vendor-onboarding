"""A stand-in for the Claude API, so the full AI path can be exercised without a key or any cost."""
import base64
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymupdf


class Handler(BaseHTTPRequestHandler):
    delay = 0.0
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

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Handler.requests.append({"path": self.path, "headers": dict(self.headers), "body": request})
        time.sleep(Handler.delay)
        content = request["messages"][0]["content"]
        if isinstance(content, str):
            text = "Good news: your details and documents look right. We just need a little more to finish."
        else:
            text = json.dumps(self._read(content[0]))
        self._reply({"id": "msg_stub", "type": "message", "role": "assistant", "model": request["model"],
                     "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                     "usage": {"input_tokens": 1000, "output_tokens": 100}})

    @staticmethod
    def _read(block):
        """Pretends to be the AI. For the scan it returns what the sample letter says."""
        data = base64.b64decode(block["source"]["data"])
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
        fields = dict(doc_type="other", legal_name=None, tax_id=None, account_holder=None, account_number=None, bank_code=None,
                      bank_name=None, issue_date=None, legibility="high", contains_instructions_to_reader=False, notes=None)
        if not text.strip():
            return {**fields, "doc_type": "bank_letter", "account_holder": "Thames Valley Fabrication Limited",
                    "account_number": "GB82WEST12345698765432", "bank_code": "GB82WEST12345698765432",
                    "bank_name": "Westminster Commercial Bank", "issue_date": "2026-10-12"}
        from app.documents import read_with_text_parser
        return {**read_with_text_parser(data, "application/pdf").model_dump(), "legibility": "high"}


def serve(port=0, delay=0.0):
    Handler.delay = delay
    Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    sys.path.insert(0, ".")
    print("stub on", serve(int(sys.argv[1]), float(sys.argv[2])).server_address, flush=True)
    threading.Event().wait()
