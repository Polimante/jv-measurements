import winsound

try:
    from playsound import playsound as _playsound
except ImportError:
    _playsound = None


def play_completion_sound():
    path = r"C:\Users\aspol\Desktop\JV measurements - Lucas Polimante - v0\sounds\success.mp3"
    try:
        if _playsound is not None:
            _playsound(path)
        else:
            raise RuntimeError("playsound not installed")
    except Exception:
        winsound.Beep(1000, 500)
