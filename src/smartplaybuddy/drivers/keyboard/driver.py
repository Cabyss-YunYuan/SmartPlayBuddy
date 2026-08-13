from drivers.base import BaseDriver
import pyautogui

pyautogui.PAUSE = 0


class KeyboardDriver(BaseDriver):
    name = "keyboard"
    description = "Keyboard driver (pyautogui)"

    def operate(self, command: str, params: dict):
        op = params["operate"]
        if op == "tap":
            pyautogui.press(params["key"])
        elif op == "combo":
            pyautogui.hotkey(*params["keys"])
        elif op == "press":
            pyautogui.keyDown(params["key"])
        elif op == "release":
            pyautogui.keyUp(params["key"])
