import vgamepad

class Gamepad:
    def __init__(self):
        self.gamepad = vgamepad.VX360Gamepad()
        self.RESET()

    def operate(self, **option):
        if option["type"] == "joystick":

            if not option.get("x"):
                option["x"] = 0
            if not option.get("y"):
                option["y"] = 0
            eval("self.gamepad." + option["joystick"].lower() + "_float(" + str(option["x"]) + ", " + str(option["y"]) + ")")
        elif option["type"] == "button":
            if option["button"].lower() in ["left_trigger", "right_trigger"]:
                if not option.get("value"):
                    if option.get("operation") == "release":
                        option["value"] = 0
                    elif option.get("operation") == "press":
                        option["value"] = 1
                eval("self.gamepad." + option["button"].lower() + "_float(" + str(option["value"]) + ")")
            else:
                eval("self.gamepad." + option["operation"] + "_button(vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_" + option["button"].upper() + ")")
        self.gamepad.update()

    def RESET(self):
        self.gamepad.reset()
        self.gamepad.update()

    def __del__(self):
        del self.gamepad
