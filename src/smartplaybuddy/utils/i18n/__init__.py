"""
国际化模块。
提供全局翻译器实例和便捷的 translate() 函数。
"""
from .translator import Translator


translator = Translator()

def set_language(language: str = "zh_CN"):
    """切换当前语言。"""
    translator.set_language(language)

def translate(keys: str, *args, **kwargs):
    """翻译键值查找，支持 {placeholder} 格式化。"""
    return translator.translate(keys, *args, **kwargs)

def reload():
    """热重载语言包：重新扫描 locales 目录并刷新当前语言和默认语言的翻译内容。"""
    translator.reload()
