import json
import importlib.resources
from .. import log

logger = log.logger.logger.getChild("Translator")

class Translator:
    language: str = "zh_CN"
    translation: dict
    default: dict
    def __init__(self, language: str = "zh_CN"):
        self.language = language
        self.locales_dir = importlib.resources.files(__package__).joinpath("locales")
        self.languages = []
        for file in self.locales_dir.iterdir():
            if file.name.endswith(".json"):
                self.languages.append(file.name[:-5])

        self.set_language(language)

        if "en_US" in self.languages:
            with self.locales_dir.joinpath("en_US.json").open("r", encoding="utf-8") as f:
                self.default = json.load(f)
        else:
            logger.warning(f"未找到语言包 en_US，默认语言包将使用 {self.language}")
            self.default = self.translation

    def set_language(self, language: str):
        language = language.replace("-", "_")
        if language not in self.languages:
            raise ValueError(language)
        self.language = language
        with self.locales_dir.joinpath(f"{language}.json").open("r", encoding="utf-8") as f:
            self.translation = json.load(f)

    def translate(self, keys: str, *args, **kwargs):
        for (lang, text) in [(self.language, self.translation), ("default", self.default)]:
            for key in keys.split("."):
                if not key or key not in text:
                    break
                text = text[key]
            else:
                if type(text) == str:
                    if kwargs:
                        return str(text).format(**kwargs)
                    elif args:
                        return str(text).format(*args)
                    else:
                        return str(text)
            logger.getChild(lang).warning(f"未找到翻译 {keys}")
        logger.error(f"未找到翻译 {keys}")
        raise KeyError(keys)
