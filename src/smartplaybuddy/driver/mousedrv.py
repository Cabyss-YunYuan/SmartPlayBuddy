import pyautogui


class MouseDrv:
    mouse = pyautogui
    width, height = pyautogui.size()

    def convert(self, x: float, y: float):
        return x * self.width, y * self.height

    def move(self, x: float, y: float, duration: float|int = 0):
        self.mouse.move(self.convert(x, y), duration=duration)

    def move_to(self, x: float, y: float, duration: float|int = 0):
        self.mouse.moveTo(self.convert(x, y), duration=duration)

    def tap(self, button = "left"):
        self.mouse.click(button=button)

    def press(self, button = "left"):
        self.mouse.mouseDown(button=button)

    def release(self, button = "left"):
        self.mouse.mouseUp(button=button)

    def scroll(self, y: float):
        self.mouse.scroll(int(self.convert(0, y)[1]))
