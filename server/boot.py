# This file is executed on every boot (including wake-boot from deepsleep)
import gc
import ubinascii
import config

def do_connect():
    import network
    sta_if = network.WLAN(network.STA_IF)
    ap_if = network.WLAN(network.AP_IF)
    if not sta_if.isconnected():
        print('connecting to network...')
        ap_if.active(False)
        sta_if.active(True)
        sta_if.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        while not sta_if.isconnected():
            pass
    print('network config:', sta_if.ifconfig())
    print('mac address:', ubinascii.hexlify(sta_if.config('mac'),':').decode())

do_connect()
gc.collect()

# Start if not already started
if config.AUTOSTART_WEBREPL:
    import webrepl
    if not webrepl.listen_s:
        webrepl.start()

import urpc
