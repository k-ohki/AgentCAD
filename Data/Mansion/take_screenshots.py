"""マンションのアセンブリを開いて画面を撮る。cd core && python ../Data/Mansion/take_screenshots.py <出力先>（環境変数 SHOTS=01,02,… で一部だけ）"""
import os, sys, threading, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from http.server import ThreadingHTTPServer
from cad.api.rpc import Api
from cad.api.server import make_handler
from cad.doc.workspace import Workspace
from playwright.sync_api import sync_playwright
os.environ["AGENTCAD_NO_DIALOG"] = "1"
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
DATA = HERE
api = Api(Workspace("usd_brep"))
t = time.time()
asm = api.call("doc.open", {"path": f"{DATA}/Mansion_asm.usda"})["doc"]
print("open", time.time() - t, flush=True)
lst = api.call("doc.list")
H = make_handler(api, threading.Lock()); H.log_message = lambda *a: None
httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{httpd.server_address[1]}/"
print(str(lst)[:300]); docs = {d["name"]: d.get("doc") for d in (lst["docs"] if isinstance(lst, dict) else lst)}
print(docs)
exec(open(os.path.join(os.path.dirname(__file__), "take_screenshots_body.py")).read())
httpd.shutdown()
