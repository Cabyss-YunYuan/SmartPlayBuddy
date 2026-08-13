from drivers.base import BaseDriver
import pyautogui


class MouseDriver(BaseDriver):
    name = "mouse"
    description = "Mouse driver (pyautogui)"

    def _convert(self, x: float, y: float):
        w, h = pyautogui.size()
        return x * w, y * h

    def operate(self, command: str, params: dict):
        op = params["operate"]
        if op == "move":
            x, y = self._convert(float(params["x"]), float(params["y"]))
            pyautogui.move(x, y, duration=float(params.get("duration", 0)))
        elif op == "move_to":
            x, y = self._convert(float(params["x"]), float(params["y"]))
            pyautogui.moveTo(x, y, duration=float(params.get("duration", 0)))
        elif op == "tap":
            pyautogui.click(button=params.get("button", "left"))
        elif op == "press":
            pyautogui.mouseDown(button=params.get("button", "left"))
        elif op == "release":
            pyautogui.mouseUp(button=params.get("button", "left"))
        elif op == "scroll":
            _, dy = self._convert(0, float(params["y"]))
            pyautogui.scroll(int(dy))
