import pyautogui


pyautogui.PAUSE = 0

class KeyboardDrv:
    keyboard = pyautogui

    def tap(self, key):
        self.keyboard.press(key)

    def combo(self, keys):
        self.keyboard.hotkey(*keys)

    def press(self, key):
        self.keyboard.keyDown(key)

    def release(self, key):
        self.keyboard.keyUp(key)
