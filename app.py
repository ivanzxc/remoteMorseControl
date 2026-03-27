from flask import Flask, request, jsonify, render_template
import serial
import serial.tools.list_ports
import threading
import time
from collections import deque
from pathlib import Path

app = Flask(__name__)

# =========================
# CONFIG
# =========================
CONFIG_FILE = Path("ui_config.json")
MAX_LOG = 400
SERIAL_TIMEOUT = 0.1

DEFAULT_CONFIG = {
    "port": "",
    "baud": 115200,
    "presets": [
        "CQ CQ DE IVAN",
        "TEST TEST",
        "SOS",
        "DE IVAN K",
    ],
    "radios": {
        "vhf": {
            "wpm": 15,
            "tone": 700,
            "text": "CQ CQ DE IVAN",
            "beacon_interval_ms": 60000,
            "ptt": False,
            "beacon_enabled": False,
            "busy": False,
        },
        "uhf": {
            "wpm": 15,
            "tone": 700,
            "text": "CQ CQ DE IVAN",
            "beacon_interval_ms": 60000,
            "ptt": False,
            "beacon_enabled": False,
            "busy": False,
        },
    },
}

RADIO_IDS = ["vhf", "uhf"]

# =========================
# ESTADO GLOBAL
# =========================
config_lock = threading.Lock()
serial_lock = threading.Lock()
log_lock = threading.Lock()

ser = None
serial_reader_thread = None
serial_reader_running = False

logs = deque(maxlen=MAX_LOG)

state = {
    "connected": False,
    "port": "",
    "baud": 115200,
    "active_radio": None,
    "radios": {
        "vhf": {
            "ptt": False,
            "busy": False,
            "beacon_enabled": False,
            "wpm": 15,
            "tone": 700,
            "text": "",
            "beacon_interval_ms": 60000,
        },
        "uhf": {
            "ptt": False,
            "busy": False,
            "beacon_enabled": False,
            "wpm": 15,
            "tone": 700,
            "text": "",
            "beacon_interval_ms": 60000,
        },
    },
}


# =========================
# UTIL
# =========================
def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with log_lock:
        logs.append(line)


def clamp_int(value, default, min_value, max_value):
    try:
        n = int(value)
    except Exception:
        return default
    return max(min_value, min(max_value, n))


def normalize_text(text: str) -> str:
    text = str(text).strip()
    return text.replace("|", "/")


def load_config():
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        import json
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return DEFAULT_CONFIG.copy()

    merged = DEFAULT_CONFIG.copy()
    merged["port"] = data.get("port", DEFAULT_CONFIG["port"])
    merged["baud"] = data.get("baud", DEFAULT_CONFIG["baud"])
    merged["presets"] = data.get("presets", DEFAULT_CONFIG["presets"])

    merged["radios"] = {"vhf": {}, "uhf": {}}
    for rid in RADIO_IDS:
        base = DEFAULT_CONFIG["radios"][rid].copy()
        base.update(data.get("radios", {}).get(rid, {}))
        base["wpm"] = clamp_int(base.get("wpm", 15), 15, 5, 60)
        base["tone"] = clamp_int(base.get("tone", 700), 700, 200, 2000)
        base["beacon_interval_ms"] = clamp_int(base.get("beacon_interval_ms", 60000), 60000, 1000, 86400000)
        base["text"] = str(base.get("text", "CQ CQ DE IVAN")).strip() or "CQ CQ DE IVAN"
        merged["radios"][rid] = base

    return merged


def save_config(cfg):
    import json
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


cfg = load_config()
state["port"] = cfg["port"]
state["baud"] = cfg["baud"]

for rid in RADIO_IDS:
    state["radios"][rid]["wpm"] = cfg["radios"][rid]["wpm"]
    state["radios"][rid]["tone"] = cfg["radios"][rid]["tone"]
    state["radios"][rid]["text"] = cfg["radios"][rid]["text"]
    state["radios"][rid]["beacon_interval_ms"] = cfg["radios"][rid]["beacon_interval_ms"]
    state["radios"][rid]["beacon_enabled"] = cfg["radios"][rid].get("beacon_enabled", False)
    state["radios"][rid]["ptt"] = False
    state["radios"][rid]["busy"] = False


def update_radio_config(radio, *, text=None, wpm=None, tone=None, beacon_interval_ms=None, beacon_enabled=None):
    with config_lock:
        if text is not None:
            text = str(text).strip()
            cfg["radios"][radio]["text"] = text
            state["radios"][radio]["text"] = text

        if wpm is not None:
            wpm = clamp_int(wpm, 15, 5, 60)
            cfg["radios"][radio]["wpm"] = wpm
            state["radios"][radio]["wpm"] = wpm

        if tone is not None:
            tone = clamp_int(tone, 700, 200, 2000)
            cfg["radios"][radio]["tone"] = tone
            state["radios"][radio]["tone"] = tone

        if beacon_interval_ms is not None:
            beacon_interval_ms = clamp_int(beacon_interval_ms, 60000, 1000, 86400000)
            cfg["radios"][radio]["beacon_interval_ms"] = beacon_interval_ms
            state["radios"][radio]["beacon_interval_ms"] = beacon_interval_ms

        if beacon_enabled is not None:
            cfg["radios"][radio]["beacon_enabled"] = bool(beacon_enabled)
            state["radios"][radio]["beacon_enabled"] = bool(beacon_enabled)

        save_config(cfg)


def list_ports():
    result = []
    for p in serial.tools.list_ports.comports():
        result.append({
            "device": p.device,
            "description": p.description or "",
        })
    return result


def send_line(line: str):
    global ser
    if not state["connected"] or ser is None or not ser.is_open:
        raise RuntimeError("Arduino no conectado")

    payload = (line.strip() + "\n").encode("utf-8")
    with serial_lock:
        ser.write(payload)
        ser.flush()
    log(f"TX {line}")


def parse_device_line(line: str):
    line = line.strip()
    if not line:
        return

    log(f"RX {line}")

    parts = line.split("|")
    head = parts[0].upper()

    if head == "READY":
        return

    if head == "PTT" and len(parts) >= 3:
        radio = parts[1].lower()
        value = parts[2].upper() == "ON"
        if radio in RADIO_IDS:
            state["radios"][radio]["ptt"] = value
        return

    if head == "TX" and len(parts) >= 3:
        radio = parts[1].lower()
        status = parts[2].upper()
        if radio in RADIO_IDS:
            if status == "START":
                state["active_radio"] = radio
                state["radios"][radio]["busy"] = True
            elif status in ("DONE", "STOPPED", "ERROR"):
                state["radios"][radio]["busy"] = False
                if state["active_radio"] == radio:
                    state["active_radio"] = None
        return

    if head == "BUSY" and len(parts) >= 2:
        active = parts[1].lower()
        if active in RADIO_IDS:
            state["active_radio"] = active
            state["radios"][active]["busy"] = True
        return

    if head == "BEACON" and len(parts) >= 3:
        radio = parts[1].lower()
        value = parts[2].upper() == "ON"
        if radio in RADIO_IDS:
            state["radios"][radio]["beacon_enabled"] = value
            cfg["radios"][radio]["beacon_enabled"] = value
            save_config(cfg)
        return

    if head == "STATUS":
        for part in parts[1:]:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.upper()
            v = v.upper()

            if k == "ACTIVE":
                state["active_radio"] = None if v == "NONE" else v.lower()
            elif k == "PTT_VHF":
                state["radios"]["vhf"]["ptt"] = (v == "ON")
            elif k == "PTT_UHF":
                state["radios"]["uhf"]["ptt"] = (v == "ON")
            elif k == "BEACON_VHF":
                state["radios"]["vhf"]["beacon_enabled"] = (v == "ON")
            elif k == "BEACON_UHF":
                state["radios"]["uhf"]["beacon_enabled"] = (v == "ON")
        return


def serial_reader_loop():
    global serial_reader_running, ser

    while serial_reader_running:
        try:
            if ser is None or not ser.is_open:
                time.sleep(0.2)
                continue

            raw = ser.readline()
            if not raw:
                continue

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                line = repr(raw)

            parse_device_line(line)

        except Exception as e:
            log(f"ERR reader: {e}")
            time.sleep(0.4)


def start_reader():
    global serial_reader_thread, serial_reader_running

    if serial_reader_running:
        return

    serial_reader_running = True
    serial_reader_thread = threading.Thread(target=serial_reader_loop, daemon=True)
    serial_reader_thread.start()


def stop_reader():
    global serial_reader_running
    serial_reader_running = False


def connect_serial(port: str, baud: int):
    global ser

    if not port:
        raise RuntimeError("Elegí un puerto")

    disconnect_serial()

    log(f"Conectando a {port} @ {baud}...")
    ser = serial.Serial(port, baud, timeout=SERIAL_TIMEOUT)
    time.sleep(2.0)

    state["connected"] = True
    state["port"] = port
    state["baud"] = baud
    cfg["port"] = port
    cfg["baud"] = baud
    save_config(cfg)

    start_reader()
    send_line("STATUS")
    log("Arduino conectado")


def disconnect_serial():
    global ser

    stop_reader()

    try:
        if ser is not None and ser.is_open:
            send_line("PTT|VHF|OFF")
            send_line("PTT|UHF|OFF")
            send_line("BEACON|VHF|OFF")
            send_line("BEACON|UHF|OFF")
            time.sleep(0.1)
            ser.close()
    except Exception:
        pass

    ser = None
    state["connected"] = False
    state["active_radio"] = None
    for rid in RADIO_IDS:
        state["radios"][rid]["ptt"] = False
        state["radios"][rid]["busy"] = False


@app.route("/")
def index():
    return render_template("index.html", config=cfg)


@app.route("/api/ports")
def api_ports():
    return jsonify({"ok": True, "ports": list_ports()})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.get_json(force=True, silent=True) or {}
    port = str(data.get("port", "")).strip()
    baud = clamp_int(data.get("baud", 115200), 115200, 1200, 2000000)

    connect_serial(port, baud)
    return jsonify({"ok": True})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    disconnect_serial()
    return jsonify({"ok": True})


@app.route("/api/status", methods=["POST"])
def api_status():
    send_line("STATUS")
    return jsonify({"ok": True})


@app.route("/api/radios")
def api_radios():
    radios_payload = {}
    for rid in RADIO_IDS:
        radios_payload[rid] = {
            "connected": state["connected"],
            "port": state["port"],
            "baud": state["baud"],
            "ptt": state["radios"][rid]["ptt"],
            "busy": state["radios"][rid]["busy"],
            "beacon_enabled": state["radios"][rid]["beacon_enabled"],
            "wpm": state["radios"][rid]["wpm"],
            "tone": state["radios"][rid]["tone"],
            "text": state["radios"][rid]["text"],
            "beacon_interval_ms": state["radios"][rid]["beacon_interval_ms"],
        }

    return jsonify({
        "ok": True,
        "connected": state["connected"],
        "active_radio": state["active_radio"],
        "radios": radios_payload,
    })


@app.route("/api/log")
def api_log():
    with log_lock:
        return jsonify({"ok": True, "log": list(logs)})


@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.get_json(force=True, silent=True) or {}
    radio = str(data.get("radio", "")).strip().lower()
    text = str(data.get("text", "")).strip()
    wpm = clamp_int(data.get("wpm", 15), 15, 5, 60)
    tone = clamp_int(data.get("tone", 700), 700, 200, 2000)

    if radio not in RADIO_IDS:
        return jsonify({"ok": False, "error": "radio inválida"}), 400
    if not text:
        return jsonify({"ok": False, "error": "texto vacío"}), 400

    update_radio_config(radio, text=text, wpm=wpm, tone=tone)

    safe_text = normalize_text(text)
    send_line(f"SEND|{radio.upper()}|{wpm}|{tone}|{safe_text}")
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json(force=True, silent=True) or {}
    radio = str(data.get("radio", "")).strip().lower()

    if radio not in RADIO_IDS:
        return jsonify({"ok": False, "error": "radio inválida"}), 400

    send_line(f"STOP|{radio.upper()}")
    return jsonify({"ok": True})


@app.route("/api/ptt", methods=["POST"])
def api_ptt():
    data = request.get_json(force=True, silent=True) or {}
    radio = str(data.get("radio", "")).strip().lower()
    mode = str(data.get("state", "")).strip().upper()

    if radio not in RADIO_IDS:
        return jsonify({"ok": False, "error": "radio inválida"}), 400
    if mode not in ("ON", "OFF"):
        return jsonify({"ok": False, "error": "state inválido"}), 400

    send_line(f"PTT|{radio.upper()}|{mode}")
    return jsonify({"ok": True})


@app.route("/api/beacon", methods=["POST"])
def api_beacon():
    data = request.get_json(force=True, silent=True) or {}
    radio = str(data.get("radio", "")).strip().lower()
    enabled = bool(data.get("enabled", False))
    text = str(data.get("text", "")).strip()
    wpm = clamp_int(data.get("wpm", 15), 15, 5, 60)
    tone = clamp_int(data.get("tone", 700), 700, 200, 2000)
    interval_ms = clamp_int(data.get("interval_ms", 60000), 60000, 1000, 86400000)

    if radio not in RADIO_IDS:
        return jsonify({"ok": False, "error": "radio inválida"}), 400

    update_radio_config(
        radio,
        text=text,
        wpm=wpm,
        tone=tone,
        beacon_interval_ms=interval_ms,
        beacon_enabled=enabled,
    )

    safe_text = normalize_text(text)
    onoff = "ON" if enabled else "OFF"
    send_line(f"BEACON|{radio.upper()}|{onoff}|{interval_ms}|{wpm}|{tone}|{safe_text}")
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)