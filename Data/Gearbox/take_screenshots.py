"""遊星歯車減速機のアセンブリ（組立・分解）・パート・図面を開いて撮る。cd core && python ../Data/Gearbox/take_screenshots.py [出力先]（SHOTS=1,2 で一部だけ）"""
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
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Gearbox_asm.usda")})["doc"]
    exp = api.call("doc.open", {"path": os.path.join(HERE, "Gearbox_exploded.usda")})["doc"]
    parts = {d["name"]: d["doc"] for d in api.call("doc.list", {})}
    drw = None
    if os.path.exists(os.path.join(HERE, "Gearbox_drawing.usda")):
        drw = api.call("doc.open", {"path": os.path.join(HERE, "Gearbox_drawing.usda")})["doc"]
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
        C = [0, 0, 10]
        if want("1"):
            cam(-38, 22, C, 360); shot("g1_hero", HIDE + STUDIO)
        if want("2"):
            cam(-25, 14, [0, 0, 0], 400, section={"normal": [0, 0, 1], "offset": 8}); shot("g2_gear_train", HIDE + NIGHT)
        if want("3"):
            cam(62, 16, C, 430, section={"normal": [1, 0, 0], "offset": 0}); shot("g3_section", HIDE + STUDIO)
        if want("4"):
            cam(150, 20, C, 430); shot("g4_input_side", HIDE + STUDIO)
        if want("8"):
            cam(-35, 22, C, 460, ortho=True); shot("g8_ui_assembly")
        if want("5") or want("6") or want("7"):
            open_doc(exp)
        E = [0, 0, 60]
        if want("5"):
            cam(-58, 16, [0, 0, 95], 860); shot("g5_exploded", HIDE + STUDIO)
        if want("6"):
            cam(-122, 14, [0, 0, 95], 860); shot("g6_exploded_night", HIDE + NIGHT)
        if want("7"):
            cam(-58, 16, [0, 0, 95], 860, style="hlr"); shot("g7_exploded_lines", HIDE)
        if want("9") and "RingGear" in parts:
            open_doc(parts["RingGear"])
            cam(-30, 20, [0, 0, 8], 260, ortho=True); shot("g9_ui_ring_gear")
        if want("10") and drw:
            open_doc(drw); time.sleep(2); shot("g10_ui_drawing")
        b.close()
    httpd.shutdown()


if __name__ == "__main__":
    main()
