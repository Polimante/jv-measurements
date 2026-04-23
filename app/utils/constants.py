import threading

APP_VERSION = "2.6.0"
APP_NAME    = f"Solar Cell J-V Measurement v{APP_VERSION}"

# Global stop flag used by the measurement thread and instrument helpers.
stop_event = threading.Event()
