"""
PCBs and Prompts — ESP32-C3 SuperMini <-> Lua AI agent. No peripherals required.

Everything here uses only what is already on the board:

    GPIO8   onboard blue LED (active-low)   -> led_on / led_off / blink / set_brightness
    GPIO9   BOOT button (0 while pressed)   -> fires a "button_pressed" trigger
    chip    internal temperature sensor     -> read_temperature
    radio   Wi-Fi                           -> get_status (RSSI, IP, uptime, free memory)

Every @device.command becomes a tool the agent can call from chat, named
device__<device_name>__<command>. Commands appear automatically; no agent code needed.
Triggers DO need a handler in the agent project (defineDeviceTrigger) before they do
anything. Until then you just see "Trigger ACK" in the log, which still proves the pipe.

Once copied to the board as main.py this runs at every boot.
Get the REPL back with Ctrl-C in `mpremote repl`, or remove it with `mpremote rm :main.py`.
"""

import gc
import time
import network
from machine import Pin, PWM

try:
    import config
except ImportError:
    raise SystemExit("config.py is missing: copy firmware/config.example.py to config.py, "
                     "fill it in, then `mpremote cp firmware/config.py :config.py`")

from lua_device import LuaDevice

# ---------------------------------------------------------------- hardware
# The LED is driven with PWM so brightness and on/off share the pin without conflicts.
# Active-low: duty 65535 (pin high) = OFF, duty 0 = fully ON.
led = PWM(Pin(8), freq=1000, duty_u16=65535)
button = Pin(9, Pin.IN, Pin.PULL_UP)
_boot_ms = time.ticks_ms()


def led_set(on):
    led.duty_u16(0 if on else 65535)


def led_brightness(percent):
    percent = max(0, min(100, int(percent)))
    led.duty_u16(int(65535 * (100 - percent) / 100))


def mcu_temperature_c():
    """Internal sensor. ESP32-C3/S2/S3/C6 report Celsius via esp32.mcu_temperature()."""
    try:
        import esp32
        return esp32.mcu_temperature()
    except (ImportError, AttributeError):
        return None


# ---------------------------------------------------------------- Wi-Fi
# Joined here rather than by the Lua client so TX power can be lowered first
# (the SuperMini's antenna layout makes full power flaky on many boards).
def connect_wifi(timeout_s=30):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if getattr(config, "WIFI_TX_POWER_DBM", None) is not None:
        try:
            wlan.config(txpower=config.WIFI_TX_POWER_DBM)
        except Exception as e:
            print("[wifi] txpower not applied:", e)
    if not wlan.isconnected():
        print("[wifi] connecting to", config.WIFI_SSID, end="")
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(timeout_s):
            if wlan.isconnected():
                break
            led_set(True)
            time.sleep_ms(100)
            led_set(False)
            time.sleep_ms(900)
            print(".", end="")
    if wlan.isconnected():
        print(" OK", wlan.ifconfig()[0])
    else:
        print(" FAILED (check SSID/password, and that it is a 2.4 GHz network)")
    return wlan


wlan = connect_wifi()

# ---------------------------------------------------------------- Lua device
device = LuaDevice(
    agent_id=config.AGENT_ID,
    api_key=config.DEVICE_CREDENTIAL,
    device_name=config.DEVICE_NAME,
    wifi_ssid=config.WIFI_SSID,        # lets the client re-join Wi-Fi during its own reconnects
    wifi_password=config.WIFI_PASSWORD,
)


@device.command("led_on", description="Turn the onboard LED on")
def cmd_led_on(payload):
    led_set(True)
    return {"led": "on"}


@device.command("led_off", description="Turn the onboard LED off")
def cmd_led_off(payload):
    led_set(False)
    return {"led": "off"}


@device.command(
    "blink",
    description="Blink the onboard LED a number of times",
    inputSchema={
        "type": "object",
        "properties": {
            "times": {"type": "integer", "minimum": 1, "maximum": 20},
            "interval_ms": {"type": "integer", "minimum": 50, "maximum": 2000},
        },
    },
)
def cmd_blink(payload):
    times = int(payload.get("times", 3))
    interval = int(payload.get("interval_ms", 200))
    for _ in range(times):
        led_set(True)
        time.sleep_ms(interval)
        led_set(False)
        time.sleep_ms(interval)
    return {"blinked": times, "interval_ms": interval}


@device.command(
    "set_brightness",
    description="Set the onboard LED brightness from 0 (off) to 100 (full)",
    inputSchema={
        "type": "object",
        "properties": {"percent": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["percent"],
    },
)
def cmd_set_brightness(payload):
    p = max(0, min(100, int(payload.get("percent", 50))))
    led_brightness(p)
    return {"brightness": p}


@device.command("read_temperature", description="Read the chip's internal temperature in celsius")
def cmd_read_temperature(payload):
    t = mcu_temperature_c()
    if t is None:
        return {"error": "temperature sensor not available in this firmware"}
    return {"temperature": t, "unit": "celsius"}


@device.command("get_status", description="Report uptime, Wi-Fi signal strength, IP and free memory")
def cmd_get_status(payload):
    try:
        rssi = wlan.status("rssi")
    except Exception:
        rssi = None
    return {
        "uptime_s": time.ticks_diff(time.ticks_ms(), _boot_ms) // 1000,
        "rssi_dbm": rssi,
        "ip": wlan.ifconfig()[0] if wlan.isconnected() else None,
        "free_mem_bytes": gc.mem_free(),
        "button_held": button.value() == 0,
    }


# ---------------------------------------------------------------- main loop
# Mirrors LuaDevice.run() — poll MQTT, heartbeat every 30 s, reconnect on error —
# and adds the BOOT-button trigger. run() blocks with no hook for that, so the loop lives here.
def main():
    gc.collect()
    print("[main] free memory before connect:", gc.mem_free())
    device.connect()
    print("[main] listening for commands as", config.DEVICE_NAME)

    last_heartbeat = time.time()
    last_button = button.value()
    last_edge_ms = time.ticks_ms()
    presses = 0

    while True:
        try:
            device._client.check_msg()

            now = time.time()
            if now - last_heartbeat >= 30:
                device._client.publish(device._topic_prefix + "heartbeat", b"", qos=0)
                last_heartbeat = now

            b = button.value()
            if b != last_button and time.ticks_diff(time.ticks_ms(), last_edge_ms) > 50:
                last_edge_ms = time.ticks_ms()
                last_button = b
                if b == 0:                      # pressed (falling edge)
                    presses += 1
                    print("[main] BOOT pressed, count =", presses)
                    device.trigger("button_pressed",
                                   {"count": presses, "temperature": mcu_temperature_c()})

            if len(device._seen_ids) > 100:
                device._clean_dedup()

            time.sleep_ms(50)

        except Exception as e:
            print("[main] connection lost:", type(e).__name__, e)
            device._reconnect()                 # blocks until reconnected; resets after 10 failures
            last_heartbeat = time.time()


main()
