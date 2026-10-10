"""
触发所有内置 section 注册。
import 本身就是副作用：每个模块顶部的 @register_section 会在导入时执行。
"""
from . import base, dashscope, llm, mongo, render, runtime, security
