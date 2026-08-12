from .keyboarddrv import KeyboardDrv
from .mousedrv import MouseDrv
from . import xbox

keyboard = KeyboardDrv()
def keyboard_operate(operate: dict):
    if operate["operate"] == "tap":
        keyboard.tap(operate["key"])
    elif operate["operate"] == "combo":
        keyboard.combo(operate["keys"])
    elif operate["operate"] == "press":
        keyboard.press(operate["key"])
    elif operate["operate"] == "release":
        keyboard.release(operate["key"])

mouse = MouseDrv()
def mouse_operate(operate: dict):
    if operate["operate"] == "move":
        mouse.move(float(operate["x"]), float(operate["y"]), duration=float(operate["duration"]))
    elif operate["operate"] == "move_to":
        mouse.move_to(float(operate["x"]), float(operate["y"]), duration=float(operate["duration"]))
    elif operate["operate"] == "tap":
        mouse.tap(operate["button"])
    elif operate["operate"] == "press":
        mouse.press(operate["button"])
    elif operate["operate"] == "release":
        mouse.release(operate["button"])
    elif operate["operate"] == "scroll":
        mouse.scroll(float(operate["y"]))

drivers = {
    "keyboard": keyboard_operate,
    "mouse": mouse_operate,
}
