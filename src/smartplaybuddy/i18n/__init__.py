from .translator import Translator


translator = Translator()

def set_language(language: str = "zh_CN"):
    translator.set_language(language)

def translate(keys: str, *args, **kwargs):
    return translator.translate(keys, *args, **kwargs)
