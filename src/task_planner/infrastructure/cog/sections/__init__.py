"""
触发所有内置 section 注册。
import 本身就是副作用：每个模块顶部的 @register_section 会在导入时执行。
"""
from . import base       # noqa: F401
from . import dashscope  # noqa: F401
from . import llm        # noqa: F401
from . import mongo      # noqa: F401
from . import web        # noqa: F401
from . import render     # noqa: F401
from . import runtime    # noqa: F401
from . import security   # noqa: F401
