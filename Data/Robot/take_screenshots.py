"""人型ロボットのアセンブリ・パート・図面を開いて撮る。cd core && python ../Data/Robot/take_screenshots.py [出力先]（SHOTS=1,2 で一部だけ）"""
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
STUDIO = "#viewport { background: radial-gradient(ellipse at 50% 40%, #f7f8fa 0%, #dfe3e9 55%, #b9c0ca 100%) !important; }"
NIGHT = "#viewport { background: radial-gradient(ellipse at 50% 35%, #3a4a63 0%, #1b2333 55%, #0b0f17 100%) !important; }"
WARM = "#viewport { background: linear-gradient(180deg, #ffe8d2 0%, #f6f1ea 60%, #e7e2da 100%) !important; }"


def main():
    api = Api(Workspace("usd_brep"))
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Robot_asm.usda")})["doc"]
    parts = {d["name"]: d["doc"] for d in api.call("doc.list", {})}
    drw = None
    if os.path.exists(os.path.join(HERE, "Robot_drawing.usda")):
        drw = api.call("doc.open", {"path": os.path.join(HERE, "Robot_drawing.usda")})["doc"]
    H = make_handler(api, threading.Lock()); H.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    want = lambda n: SHOTS == "all" or n in SHOTS.split(",")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2).new_page()
        pg.on("pageerror", lambda e: print("pageerror", e))

        def open_doc(d):
            for _ in range(3):
                pg.goto(f"{url}?doc={d}")
                try:
                    pg.wait_for_selector("body[data-ready='1']", timeout=120000)
                    break
                except Exception:
                    print("ready 待ちが切れたのでやり直します", flush=True)
            time.sleep(2.5)

        def cam(yaw, pitch, target, dist, ortho=False, style="shaded_edges", section=None):
            pg.evaluate("""([yaw, pitch, target, dist, ortho, style, section]) => {
                const v = window.__agentcad.viewer; v.setOrtho(ortho); v.setStyle(style);
                v.sectionGizmo = false; v.setSection(section);
                v.animateTo({ yaw, pitch, target, dist }, 0); v.redraw(); }""", [yaw, pitch, target, dist, ortho, style, section])
            time.sleep(1.5)

        def shot(name, css=None):
            h = pg.add_style_tag(content=css) if css else None
            if css:                                  # 宣伝用は原点の軸を消す（次に形を読み込むまで）
                pg.evaluate("() => { window.__agentcad.viewer.axes = null; }")
            time.sleep(0.8); pg.evaluate("() => window.__agentcad.viewer && window.__agentcad.viewer.redraw()"); time.sleep(1.0)
            pg.screenshot(path=os.path.join(OUT, name + ".png"))
            if h:
                h.evaluate("e => e.remove()")
            print("shot", name, flush=True)

        open_doc(asm)
        C = [0, 245, 0]
        if want("1"):
            cam(-32, 12, [0, 244, 0], 1080); shot("r1_hero", HIDE + STUDIO)
        if want("2"):
            cam(22, -14, [0, 255, 0], 1040); shot("r2_low_angle", HIDE + NIGHT)
        if want("3"):
            cam(0, 2, C, 900, ortho=True); shot("r3_front", HIDE + WARM)
        if want("4"):
            cam(148, 16, [0, 244, 0], 1080); shot("r4_back", HIDE + STUDIO)
        if want("5"):
            cam(-40, 20, [10, 120, 0], 720); shot("r5_leg_closeup", HIDE + STUDIO)
        if want("6"):
            cam(-28, 10, [0, 244, 0], 1080, section={"normal": [0, 0, 1], "offset": 0}); shot("r6_section", HIDE + STUDIO)
        if want("7"):
            cam(-30, 18, [0, 244, 0], 1100, style="hlr"); shot("r7_line_drawing", HIDE)
        if want("8"):
            cam(-30, 20, C, 1550, ortho=True); shot("r8_ui_assembly")
        if want("9") and "Servo" in parts:
            open_doc(parts["Servo"])
            cam(-35, 25, [0, -10, 0], 120, ortho=True); shot("r9_ui_servo")
        if want("10") and drw:
            open_doc(drw); time.sleep(2); shot("r10_ui_drawing")
        b.close()
    httpd.shutdown()


if __name__ == "__main__":
    main()
