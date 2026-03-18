from flask import Flask, render_template, request, jsonify
import serial
import serial.tools.list_ports
import threading
import time
import json
from pathlib import Path

app = Flask(__name__)

ser = None
ser_lock = threading.Lock()
serial_log = []
max_log = 400
CONFIG_FILE = Path("ui_config.json")


DEFAULT_CONFIG = {
    "port": "",
    "baud": 115200,
    "wpm": 15,
    "tone": 700,
    "text": "CQ CQ DE IVAN",
    "beacon_interval_ms": 60000,
    "presets": [
        "CQ CQ DE IVAN",
        "TEST TEST",
        "SOS",
        "DE IVAN K"
    ]
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(data):
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


ui_config = load_config()


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    serial_log.append(f"[{ts}] {msg}")
    if len(serial_log) > max_log:
        del serial_log[:len(serial_log) - max_log]


def list_ports():
    return [{"device": p.device, "description": p.description} for p in serial.tools.list_ports.comports()]


def serial_reader():
    global ser
    while True:
        try:
            if ser and ser.is_open:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    log(f"ARDUINO -> {line}")
            else:
                time.sleep(0.2)
        except Exception as e:
            log(f"ERR reader: {e}")
            time.sleep(0.5)


threading.Thread(target=serial_reader, daemon=True).start()


def write_serial(cmd: str):
    global ser
    with ser_lock:
        if not ser or not ser.is_open:
            raise RuntimeError("No conectado")
        ser.write((cmd + "\n").encode("utf-8"))
        ser.flush()
        log(f"PC -> {cmd}")


@app.route("/")
def index():
    return render_template("index.html", config=ui_config)


@app.route("/api/ports")
def api_ports():
    return jsonify({"ok": True, "ports": list_ports()})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global ui_config
    if request.method == "GET":
        return jsonify({"ok": True, "config": ui_config})

    data = request.json or {}
    ui_config = {**ui_config, **data}
    save_config(ui_config)
    return jsonify({"ok": True, "config": ui_config})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    global ser, ui_config
    data = request.json or {}
    port = data.get("port")
    baud = int(data.get("baud", 115200))

    if not port:
        return jsonify({"ok": False, "error": "Puerto faltante"}), 400

    try:
        with ser_lock:
            if ser and ser.is_open:
                ser.close()
            ser = serial.Serial(port, baudrate=baud, timeout=0.5)
            time.sleep(2.0)

        ui_config["port"] = port
        ui_config["baud"] = baud
        save_config(ui_config)

        log(f"Conectado a {port} @ {baud}")
        write_serial("PING")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    global ser
    try:
        with ser_lock:
            if ser and ser.is_open:
                ser.close()
                log("Puerto serial desconectado")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/send", methods=["POST"])
def api_send():
    global ui_config
    data = request.json or {}
    text = str(data.get("text", "")).strip()
    wpm = int(data.get("wpm", 15))
    tone = int(data.get("tone", 700))

    if not text:
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    try:
        write_serial(f"SEND|{text}|{wpm}|{tone}")
        ui_config["text"] = text
        ui_config["wpm"] = wpm
        ui_config["tone"] = tone
        save_config(ui_config)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    try:
        write_serial("STOP")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ptt", methods=["POST"])
def api_ptt():
    data = request.json or {}
    state = data.get("state", "OFF").upper()
    if state not in ("ON", "OFF"):
        return jsonify({"ok": False, "error": "state inválido"}), 400
    try:
        write_serial(f"PTT|{state}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/status", methods=["POST"])
def api_status():
    try:
        write_serial("STATUS")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/beacon", methods=["POST"])
def api_beacon():
    global ui_config
    data = request.json or {}
    enabled = bool(data.get("enabled", False))
    text = str(data.get("text", "")).strip()
    wpm = int(data.get("wpm", 15))
    tone = int(data.get("tone", 700))
    interval_ms = int(data.get("interval_ms", 60000))

    try:
        if enabled:
            if not text:
                return jsonify({"ok": False, "error": "Texto beacon vacío"}), 400
            write_serial(f"BEACON|ON|{text}|{wpm}|{tone}|{interval_ms}")
        else:
            write_serial("BEACON|OFF")

        ui_config["text"] = text or ui_config.get("text", "")
        ui_config["wpm"] = wpm
        ui_config["tone"] = tone
        ui_config["beacon_interval_ms"] = interval_ms
        save_config(ui_config)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/log")
def api_log():
    return jsonify({"ok": True, "log": serial_log[-160:]})
    

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
