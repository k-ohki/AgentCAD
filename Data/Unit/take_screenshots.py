"""住戸のアセンブリを開いて 3D 見取り図を撮る。cd core && python ../Data/Unit/take_screenshots.py [出力先]（SHOTS=1,2 で一部だけ）"""
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
os.environ["AGENTCAD_NO_DIALOG"] = "1"
from cad.api.rpc import Api  # noqa: E402
from cad.api.server import make_handler  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "screenshots")
SHOTS = os.environ.get("SHOTS", "all")
os.makedirs(OUT, exist_ok=True)
HIDE = """header, #left, #right, #status, #ai { display: none !important; }
main { grid-template-columns: 1fr !important; }
#viewport > :not(#view) { display: none !important; }"""
SKY = "#viewport { background: linear-gradient(180deg, #dfe8f2 0%, #f3f1ec 100%) !important; }"


def main():
    api = Api(Workspace("usd_brep"))
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Unit_asm.usda")})["doc"]
    H = make_handler(api, threading.Lock()); H.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    want = lambda n: SHOTS == "all" or n in SHOTS.split(",")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2).new_page()
        pg.on("pageerror", lambda e: print("pageerror", e))
        pg.goto(f"{url}?doc={asm}"); pg.wait_for_selector("body[data-ready='1']", timeout=300000); time.sleep(2)

        def cam(yaw, pitch, target, dist, ortho=False, style="shaded_edges"):
            pg.evaluate("""([yaw, pitch, target, dist, ortho, style]) => {
                const v = window.__agentcad.viewer; v.setOrtho(ortho); v.setStyle(style);
                v.animateTo({ yaw, pitch, target, dist }, 0); v.redraw(); }""", [yaw, pitch, target, dist, ortho, style])
            time.sleep(1.5)

        def shot(name, css=None):
            h = pg.add_style_tag(content=css) if css else None
            time.sleep(0.8); pg.evaluate("() => window.__agentcad.viewer.redraw()"); time.sleep(1.0)
            pg.screenshot(path=os.path.join(OUT, name + ".png"))
            if h:
                h.evaluate("e => e.remove()")
            print("shot", name, flush=True)
        C = [3000, 600, -4200]
        if want("1"):
            cam(32, 58, [3000, 0, -4000], 21000); shot("u1_isometric", HIDE + SKY)
        if want("2"):
            cam(160, 50, [2800, 0, -3000], 12500); shot("u2_ldk", HIDE + SKY)
        if want("3"):
            cam(0, 89.9, [3000, 0, -4100], 27000, ortho=True); shot("u3_top", HIDE + SKY)
        if want("4"):
            cam(-30, 66, [3000, 0, -7400], 12500); shot("u4_rooms_north", HIDE + SKY)
        if want("5"):
            cam(28, 40, [3000, 300, -4000], 21500, ortho=True); shot("u5_ui_assembly")
        b.close()
    httpd.shutdown()


if __name__ == "__main__":
    main()
