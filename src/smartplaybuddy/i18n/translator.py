"""
翻译器实现。
按点分路径查找翻译键，优先当前语言，回退到 en_US 默认语言。
"""
import json
import importlib.resources
from .. import log

logger = log.logger.getChild("Translator")

class Translator:
    language: str = "zh_CN"
    translation: dict
    default: dict

    def __init__(self, language: str = "zh_CN"):
        self.language = language
        # 自动扫描 locales 目录下所有 .json 语言包
        self.locales_dir = importlib.resources.files(__package__).joinpath("locales")
        self.languages = []
        for file in self.locales_dir.iterdir():
            if file.name.endswith(".json"):
                self.languages.append(file.name[:-5])

        self.set_language(language)

        # en_US 作为默认回退语言
        if "en_US" in self.languages:
            with self.locales_dir.joinpath("en_US.json").open("r", encoding="utf-8") as f:
                self.default = json.load(f)
        else:
            logger.warning(self.translate("i18n.language_pack_not_found", package1="en_US", package2=self.language))
            self.default = self.translation

    def set_language(self, language: str):
        language = language.replace("-", "_")
        if language not in self.languages:
            raise ValueError(language)
        self.language = language
        with self.locales_dir.joinpath(f"{language}.json").open("r", encoding="utf-8") as f:
            self.translation = json.load(f)

    def reload(self):
        """热重载：重新扫描语言包目录并重新加载当前语言和默认语言。"""
        old_languages = self.languages.copy()

        self.languages = []
        for file in self.locales_dir.iterdir():
            if file.name.endswith(".json"):
                self.languages.append(file.name[:-5])

        with self.locales_dir.joinpath(f"{self.language}.json").open("r", encoding="utf-8") as f:
            self.translation = json.load(f)

        if "en_US" in self.languages:
            with self.locales_dir.joinpath("en_US.json").open("r", encoding="utf-8") as f:
                self.default = json.load(f)
        else:
            self.default = self.translation

        added = set(self.languages) - set(old_languages)
        removed = set(old_languages) - set(self.languages)
        logger.debug(self.translate("i18n.reloaded",
                                    added=", ".join(added) or "无",
                                    removed=", ".join(removed) or "无"))

    def translate(self, keys: str, *args, **kwargs):
        # 优先当前语言，回退默认语言
        for (lang, text) in [(self.language, self.translation.copy()), ("default", self.default.copy())]:
            # 按 "." 分割逐级查找嵌套 key
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
            logger.getChild(lang).warning(self.translate("i18n.translation_not_found", key=keys))
        logger.error(self.translate("i18n.translation_not_found", key=keys))
        raise KeyError(keys)
