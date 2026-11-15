SHOTS = os.environ.get("SHOTS", "all")
HIDE = """header, #left, #right, #status, #ai { display: none !important; }
main { grid-template-columns: 1fr !important; }
#viewport > :not(#view) { display: none !important; }"""
SKY = "#viewport { background: linear-gradient(180deg, #9fbfe0 0%, #d8e6f3 55%, #f4efe6 100%) !important; }"
DUSK = "#viewport { background: linear-gradient(180deg, #14213d 0%, #3b4f7a 50%, #e39b6f 88%, #f2c48d 100%) !important; }"

def cam(pg, yaw, pitch, target, dist, ortho=False, style="shaded_edges", section=None):
    pg.evaluate("""([yaw, pitch, target, dist, ortho, style, section]) => {
        const v = window.__agentcad.viewer; v.setOrtho(ortho); v.setStyle(style);
        v.sectionGizmo = false; v.setSection(section);
        v.animateTo({ yaw, pitch, target, dist }, 0); v.redraw(); }""", [yaw, pitch, target, dist, ortho, style, section])
    time.sleep(1.5)

def want(n):
    return SHOTS == "all" or n in SHOTS.split(",")

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: print("pageerror", e))
    pg.on("console", lambda m: print("console", m.type, m.text[:200]) if m.type in ("error", "warning") else None)
    pg.on("requestfailed", lambda r: print("reqfail", r.url, r.failure))
    def open_doc(d):
        pg.goto(f"{url}?doc={d}")
        try:
            pg.wait_for_selector("body[data-ready='1']", timeout=60000)
        except Exception as e:
            print("ready 待ちが切れました", pg.evaluate("() => [document.body.dataset.ready, document.title, window.__agentcad && window.__agentcad.app.doc]"), flush=True)
        time.sleep(3)
    def shot(name, css=None):
        h = pg.add_style_tag(content=css) if css else None
        time.sleep(0.8)
        pg.evaluate("() => window.__agentcad.viewer.redraw()"); time.sleep(1.0)
        pg.screenshot(path=f"{OUT}/{name}.png")
        if h: h.evaluate("e => e.remove()")
        print("shot", name, flush=True)
    open_doc(asm)
    C = [12500, 7000, -4000]
    if want("01"):
        cam(pg, -34, 10, [12500, 7500, -2000], 66000); shot("01_hero_south", HIDE + SKY)
    if want("02"):
        cam(pg, 118, 9, [22000, 8500, -8000], 58000); shot("02_stair_northeast", HIDE + SKY)
    if want("03"):
        cam(pg, 28, 38, [15000, 4000, -4000], 70000); shot("03_aerial", HIDE + SKY)
    if want("04"):
        cam(pg, 196, 6, [24500, 9000, -10650], 40000, section={"normal": [0, 0, -1], "offset": 10650})
        shot("04_section_stair", HIDE + SKY)
    if want("05"):
        cam(pg, 52, 6, [14000, 8000, -4000], 68000); shot("05_dusk", HIDE + DUSK)
    if want("06"):
        cam(pg, 140, 22, [14000, 7500, -6000], 62000, style="hlr"); shot("06_line_drawing", HIDE)
    if want("07"):
        cam(pg, -35, 22, [13500, 8500, -5000], 70000, ortho=True); shot("07_ui_assembly")
    if want("08"):
        open_doc(docs["StairUnit"])
        cam(pg, -35, 48, [26300, 1500, -11300], 15000, ortho=True); shot("08_ui_stair_part")
    if want("09"):
        open_doc(docs["Building"])
        cam(pg, -32, 20, [13500, 9500, -6000], 64000, ortho=True); shot("09_ui_building_part")
    b.close()
