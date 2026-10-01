"""
llm_client.py — 异步 LLM 调用封装（OpenAI 兼容版）
支持 async/await，使用 AsyncOpenAI 客户端，含取消、重试、超时控制。
"""
import json
import re
import asyncio
import random
from json import JSONDecoder
from typing import Any, Optional
from openai import AsyncOpenAI

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import (
    MOCK_RESPONSE_PREFIX,
    VALID_INTENT_CATEGORIES,
    VALID_INTENT_COMPLEXITIES,
)
from task_planner.infrastructure.regexes import (
    SKIP_PLANNING_PATTERNS,
    THINK_RE,
    UNREPLACED_PATTERN,
)
from task_planner.infrastructure.prompts.loader import (
    EXECUTE_NODE_PROMPT,
    INTENT_PROMPT,
    NODE_REFINE_PROMPT,
    PLANNER_PROMPT,
)
DASHSCOPE_API_KEY = _hub.dev.DASHSCOPE_API_KEY
DASHSCOPE_BASE_URL = _hub.dev.DASHSCOPE_BASE_URL
LLM_INTENT_ENABLE_THINKING = _hub.dev.LLM_INTENT_ENABLE_THINKING
LLM_INTENT_MODEL = _hub.dev.LLM_INTENT_MODEL
LLM_MAX_CONCURRENT = _hub.dev.LLM_MAX_CONCURRENT
LLM_MAX_RETRIES = _hub.dev.LLM_MAX_RETRIES
LLM_NODE_ENABLE_THINKING = _hub.dev.LLM_NODE_ENABLE_THINKING
LLM_NODE_MODEL = _hub.dev.LLM_NODE_MODEL
LLM_NODE_TIMEOUT = _hub.dev.LLM_NODE_TIMEOUT
LLM_PLANNER_ENABLE_THINKING = _hub.dev.LLM_PLANNER_ENABLE_THINKING
LLM_PLANNER_MODEL = _hub.dev.LLM_PLANNER_MODEL
LLM_RETRY_BACKOFF = _hub.dev.LLM_RETRY_BACKOFF
LLM_THINKING_BUDGET = _hub.dev.LLM_THINKING_BUDGET
LLM_TIMEOUT = _hub.dev.LLM_TIMEOUT
USE_MOCK_LLM = _hub.dev.USE_MOCK_LLM
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


# ============================================================
#  业务异常定义
# ============================================================

class LLMClientError(Exception):
    """LLM 客户端基础异常"""
    def __init__(self, message: str, req_id: str | None = None):
        self.req_id = req_id
        super().__init__(f"[req={req_id}] {message}" if req_id else message)


class LLMTimeoutError(LLMClientError):
    """LLM 调用超时（统一封装 asyncio.TimeoutError / TimeoutError / httpx.TimeoutException）"""
    pass


class LLMCancelledError(LLMClientError):
    """LLM 调用被用户取消"""
    pass


class LLMResponseError(LLMClientError):
    """LLM 返回内容异常（空响应、JSON 解析失败、格式不符等）"""
    pass


# ============================================================
#  客户端单例（异步懒加载）
# ============================================================
_client: Optional[AsyncOpenAI] = None
_dashscope_available = False

# ✅ P0-3 修复：懒加载 Lock/Semaphore，避免模块级创建时绑定错误的事件循环
_client_lock: Optional[asyncio.Lock] = None
_semaphore: Optional[asyncio.Semaphore] = None


def _get_client_lock() -> asyncio.Lock:
    """懒加载协程锁（首次调用时绑定当前事件循环）"""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _get_semaphore() -> asyncio.Semaphore:
    """懒加载并发信号量（首次调用时绑定当前事件循环）"""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENT)
    return _semaphore


async def get_llm_client() -> Optional[AsyncOpenAI]:
    """异步懒加载 AsyncOpenAI 客户端（线程安全/协程安全）"""
    global _client, _dashscope_available
    if _client is not None:
        return _client

    async with _get_client_lock():
        if _client is not None:
            return _client

        if USE_MOCK_LLM or not DASHSCOPE_API_KEY:
            logger.info("[LLMClient] 启用 Mock 模式")
            return None

        try:
            base_url = DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            # 可自定义 httpx 客户端参数以调整连接池
            import httpx
            timeout = httpx.Timeout(LLM_TIMEOUT, connect=5.0)
            http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_connections=LLM_MAX_CONCURRENT * 2)
            )
            _client = AsyncOpenAI(
                api_key=DASHSCOPE_API_KEY,
                base_url=base_url,
                http_client=http_client,
            )
            _dashscope_available = True
            logger.info("[LLMClient] AsyncOpenAI 客户端就绪 (base=%s, max_conn=%d)",
                        base_url, LLM_MAX_CONCURRENT)
        except ImportError:
            logger.error("[LLMClient] openai 未安装: pip install openai")
            return None

    return _client

# ============================================================
#  工具函数（与同步版本保持一致，但无需改动）
# ============================================================

# ✅ [Fix-2] 用字符串拼接构建正则，彻底避免 HTML 标签在复制/渲染/版本管理中被吞噬
#    运行时等价于 re.compile(r'</think>.*?', re.DOTALL)
_THINK_RE = THINK_RE


# ✅ P0-1 修复：用标准库 json.JSONDecoder.raw_decode，
#    正确处理字符串转义、嵌套花括号、Unicode 等所有边界情况。
#    不再自己维护引号状态机。
# ✅ P0-1 修复（v2）：用标准库 raw_decode + 尾部锚点校验
_JSON_DECODER = JSONDecoder()


def _extract_tail_json(text: str) -> str | None:
    """
    从含纯文本思考过程的响应中提取尾部 JSON。

    策略：
      1. 定位最后一个 '}' 作为目标锚点
      2. 从右向左扫描 '{'，用 raw_decode 尝试解析
      3. 只有当解析结果的结束位置**恰好覆盖到最后一个 '}'**时，才算尾部 JSON
         （这能正确跳过嵌套的内层对象，找到最外层）
      4. 全部尝试失败 → None

    为什么用 raw_decode：
      - 正确处理字符串转义、嵌套花括号、Unicode
      - 不再自己维护引号状态机（历史 bug 根源）
    """
    if not text:
        return None

    last_rbrace = text.rfind('}')
    if last_rbrace == -1:
        return None
    target_end = last_rbrace + 1

    pos = target_end
    while True:
        start = text.rfind('{', 0, pos)
        if start == -1:
            return None
        try:
            obj, end = _JSON_DECODER.raw_decode(text, start)
            # ✅ 关键校验：必须解析到尾部锚点，否则是内层对象，跳过
            if isinstance(obj, dict) and end == target_end:
                logger.debug("[LLM] 尾部JSON提取成功, len=%d", end - start)
                return text[start:end]
        except json.JSONDecodeError:
            pass
        pos = start
def _clean_json_str(text: str) -> str:
    """更稳健的 JSON 提取：兼容 thinking 标签、纯文本思考、markdown 代码块"""
    if not text or not isinstance(text, str):
        return "{}"

    # 1. 基础清洗
    text = text.strip().lstrip('\ufeff')
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)

    # 2. 剥离 thinking 标签（标准 thinking 格式）
    #    ✅ [Fix-2] 使用预编译的 _THINK_RE + 回退保护
    _prev = text
    text = _THINK_RE.sub('', text).strip()
    if not text:
        text = _prev
        logger.warning("[LLM] _clean_json_str thinking标签剥离后文本为空，已回退")

    # 3. 剥离 Qwen 风格的纯文本思考过程(优化)
    #    特征：以"思考过程"开头，JSON 通常在最后且是独立的 {...} 块
    if text.startswith("思考过程") or text.startswith("思考："):
        logger.debug("[LLM] _clean_json_str 检测到纯文本思考前缀，启用尾部JSON提取")

        # ✅ 替换为锚点跳跃 + 状态感知的提取函数
        candidate = _extract_tail_json(text)
        if candidate is not None:
            return candidate

        # 如果提取失败，不中断流程，继续向下走 L4/L5 降级策略
        logger.warning("[LLM] _clean_json_str 尾部JSON提取失败，进入后续降级解析")

    # 4. 去除 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE).strip()

    # 5. 优先尝试直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError as e:
        logger.debug(
            "[LLM] _clean_json_str 直接解析失败: %s | text前100字符: %s", e, text[:100])

    # 6. 降级：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as e:
            logger.debug("[LLM] _clean_json_str 降级解析也失败: %s", e)

    logger.warning("[LLM] _clean_json_str 所有解析均失败 | 原始前300字符: %s", text[:300])
    return "{}"


def _extract_json(raw: str) -> dict:
    """从 LLM 原始返回中提取 JSON dict"""
    logger.debug("[LLM] _extract_json input (first 200 chars): %s", raw[:200])
    try:
        cleaned = _clean_json_str(raw)
        if not cleaned or cleaned == "{}":
            if raw.strip().startswith("{") and "needs_planning" in raw:
                logger.error(
                    "[LLMClient] _clean_json_str 丢失了有效JSON! raw前300字符: %s", raw[:300]
                )
            return {}
        result = json.loads(cleaned)
        if isinstance(result, dict):
            logger.debug("[LLM] _extract_json 成功, keys=%s",
                         list(result.keys()))
        else:
            logger.warning(
                "[LLM] _extract_json 返回值非dict: type=%s", type(result).__name__)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError as e:
        logger.warning(
            "[LLMClient] JSON 解析失败: %s | 原始前200字符: %s", e, raw[:200])
        return {}
    except Exception as e:
        logger.error("[LLMClient] _extract_json 非预期异常: %s: %s | 原始前200字符: %s",
                     type(e).__name__, e, raw[:200])
        return {}


# ✅ P0-2 修复：返回 key 与 refine_node 期望的契约对齐
#    （name / details / meta），而不是 content。
def _normalize_llm_output(raw: dict, expected_keys: list[str]) -> dict:
    """
    将 LLM 返回的不同格式归一化为统一 schema。

    兼容多种上游格式：
    - {"result": "...", "notes": "..."}          (executor 风格)
    - {"detail": "...", "meta": {...}}           (refine 单数风格)
    - {"name": "...", "details": "...", ...}     (目标契约，快速路径)

    最终输出始终对齐 expected_keys（通常为 name/details/meta）。
    """
    # 0. 入口类型守卫
    if not isinstance(raw, dict):
        logger.error(
            "[LLM] _normalize_llm_output 输入非 dict, 已拦截: type=%s, value=%s",
            type(raw).__name__, str(raw)[:100]
        )
        return {}

    # 快速路径：已经是目标格式
    if all(k in raw for k in expected_keys):
        return raw

    # P1: 兼容 {"result": "...", "notes": "..."}  → 映射到 name/details/meta
    if "result" in raw:
        result_val = raw["result"]
        if isinstance(result_val, str):
            meta = raw.get("meta", {})
            if not isinstance(meta, dict):
                logger.warning(
                    "[LLM] 'meta' 字段非 dict, 已重置为空字典: type=%s",
                    type(meta).__name__
                )
                meta = {}
            if "notes" in raw:
                meta["notes"] = raw["notes"]
            return {
                "name": raw.get("name", ""),   # LLM 给了 name 就用，否则留给上游兜底
                "details": result_val,          # ✅ 关键：用 details 而非 content
                "meta": meta,
            }
        else:
            logger.warning(
                "[LLM] 'result' 字段非字符串, 跳过 P1 适配: type=%s",
                type(result_val).__name__
            )

    # P2: 兼容 {"detail": "...", "meta": {...}}  → 映射到 name/details/meta
    if "detail" in raw:
        meta = raw.get("meta", {})
        if not isinstance(meta, dict):
            logger.warning(
                "[LLM] 'meta' 字段非 dict, 已重置为空字典: type=%s",
                type(meta).__name__
            )
            meta = {}
        return {
            "name": raw.get("name", ""),
            "details": raw["detail"],
            "meta": meta,
        }

    # P3: 兜底透传
    logger.warning(
        "[LLM] _normalize_llm_output 无法识别的输出格式: keys=%s", list(raw.keys())
    )
    return raw

# ============================================================
#  异步核心调用
# ============================================================

async def _async_call_llm(
    model: str,
    prompt: str,
    enable_thinking: bool,
    req_id: str,
    timeout: Optional[float] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> str:
    """
    异步统一 LLM 调用入口。

    - 使用 asyncio.timeout 控制超时
    - 使用 asyncio.Event 检查取消信号
    - 支持流式调用（当 enable_thinking=True 且 cancel_event 存在时）
    - 重试逻辑（指数退避）
    - 所有异常统一转换为 LLMClientError 体系
    """
    client = await get_llm_client()
    if client is None:
        raise LLMClientError("LLM 客户端未初始化，请检查 API Key", req_id)

    if timeout is None:
        timeout = LLM_NODE_TIMEOUT if enable_thinking else LLM_TIMEOUT

    last_error: Optional[Exception] = None

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        # 检查取消信号
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("任务已取消，停止 LLM 调用", req_id)

        try:
            async with asyncio.timeout(timeout):
                logger.debug(
                    "[LLMClient] 调用模型 %s (第%d次, timeout=%ss, thinking=%s, req=%s)",
                    model, attempt, timeout, enable_thinking, req_id,
                )

                extra_body: Optional[dict[str, Any]] = None
                if enable_thinking:
                    extra_body = {
                        "enable_thinking": True,
                        "thinking_budget": LLM_THINKING_BUDGET,
                    }

                content = ""

                # --- 流式分支（带取消支持） ---
                if enable_thinking and cancel_event is not None:
                    stream = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=True,
                        extra_body=extra_body,
                    )
                    reasoning_chunks: list[str] = []
                    content_chunks: list[str] = []
                    try:
                        async for chunk in stream:
                            if cancel_event.is_set():
                                raise LLMCancelledError("任务已取消，停止 LLM 流式读取", req_id)
                            if not chunk.choices:
                                continue
                            delta = chunk.choices[0].delta
                            r = getattr(delta, "reasoning_content", None)
                            c = getattr(delta, "content", None)
                            if r:
                                reasoning_chunks.append(r)
                            if c:
                                content_chunks.append(c)
                    finally:
                        if hasattr(stream, 'close'):
                            try:
                                await stream.close()
                            except Exception:
                                pass

                    content = ("".join(reasoning_chunks) + "".join(content_chunks)).strip()

                else:
                    # --- 非流式分支 ---
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False,
                        extra_body=extra_body,
                    )
                    msg = response.choices[0].message
                    text = getattr(msg, "content", None) or ""
                    reasoning = getattr(msg, "reasoning_content", None) or ""
                    if enable_thinking:
                        content = (reasoning + text).strip()
                    else:
                        content = text.strip()

                if not content:
                    raise LLMResponseError("API 返回内容为空", req_id)

                logger.info("[LLMClient] ✅ %s 成功 (attempt=%d, req=%s)", model, attempt, req_id)
                return content

        except LLMCancelledError:
            raise
        except LLMResponseError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            last_error = LLMTimeoutError(f"请求超时 ({timeout}s)", req_id)
        except Exception as e:
            last_error = e

        # 重试前等待
        # ✅ P1 修复：区分瞬态/非瞬态错误；非瞬态直接抛，不浪费重试
        except (asyncio.TimeoutError, TimeoutError):
            last_error = LLMTimeoutError(f"请求超时 ({timeout}s)", req_id)
        except Exception as e:
            err_str = str(e).lower()
            err_name = type(e).__name__

            # 4xx 类错误（认证失败、参数错误）重试没意义
            non_retryable = (
                "401" in err_str or "403" in err_str or "400" in err_str
                or "authentication" in err_str
                or "invalid_api_key" in err_str
                or "permission" in err_str
            )
            if non_retryable:
                logger.error(
                    "[LLMClient] ❌ 非瞬态错误, 不重试: %s: %s (req=%s)",
                    err_name, e, req_id,
                )
                raise LLMClientError(f"LLM 调用失败(不可重试): {e}", req_id) from e

            last_error = e

        # ✅ P1 修复：退避加全抖动（full jitter），避免高并发惊群
        base_wait = LLM_RETRY_BACKOFF ** attempt
        wait = random.uniform(0, base_wait)

        logger.warning(
            "[LLMClient] ⚠️ %s 失败 (attempt=%d/%d, wait=%.1fs, err=%s, req=%s)",
            model, attempt, LLM_MAX_RETRIES, wait, last_error, req_id,
        )

        if attempt < LLM_MAX_RETRIES:
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledError("任务已取消，停止重试", req_id) from last_error
            await asyncio.sleep(wait)
        else:
            logger.error("[LLMClient] ❌ %s 最终失败: %s (req=%s)", model, last_error, req_id)
            if isinstance(last_error, LLMTimeoutError):
                # ✅ 不要 `from last_error`（自己 cause 自己），用 from None
                raise last_error from None
            raise LLMClientError(f"LLM 调用最终失败: {last_error}", req_id) from last_error

    raise LLMClientError("LLM 调用最终失败", req_id)

# ============================================================
#  模板渲染（同步，不变）
# ============================================================

_UNREPLACED_PATTERN = UNREPLACED_PATTERN


def _render_template(template, req_id: str, **kwargs) -> str:
    """
    安全渲染 string.Template。

    ✅ 使用 safe_substitute 替代 substitute：
       - substitute 是严格模式：模板中有多余 $ 或用户输入含 $ 时会抛 ValueError
       - safe_substitute 对未匹配的占位符保留原样，不会崩溃

    ✅ 渲染后记录 prompt 尾部日志，快速发现占位符未替换问题。
    ✅ 正则同时覆盖 ${var} 和 $var 两种格式，并排除 $$ 转义。
    """
    try:
        result = template.safe_substitute(**kwargs)
        # 记录尾部 150 字符，足以看到 <user_input> 区域是否被正确替换
        tail = result[-150:].replace('\n', '\\n')
        logger.debug(
            "[LLM] _render_template 渲染完成 (req=%s) | 尾部: %s", req_id, tail)

        # ✅ 主动检测：如果渲染后仍包含未替换的占位符，发出警告
        #    findall 返回元组列表如 [('var1', ''), ('', 'var2')]，展平并过滤空串
        matches = _UNREPLACED_PATTERN.findall(result)
        unreplaced = [name for group in matches for name in group if name]
        if unreplaced:
            logger.warning(
                "[LLM] _render_template 存在未替换占位符: %s (req=%s) "
                "请检查 config.py 中模板的占位符名是否与 kwargs 匹配",
                unreplaced, req_id,
            )
        return result
    except Exception as e:
        logger.error("[LLM] _render_template 渲染失败 (req=%s): %s", req_id, e)
        raise


# ============================================================
#  公开异步接口
# ============================================================

async def direct_chat(
    user_input: str,
    req_id: str,
    cancel_event: Optional[asyncio.Event] = None,
    timeout: Optional[float] = None,
) -> str:
    """直接对话接口（异步）"""
    if USE_MOCK_LLM:
        return f"{MOCK_RESPONSE_PREFIX}{user_input}"
    async with _get_semaphore():  # 并发限制
        return await _async_call_llm(
            model=LLM_INTENT_MODEL,
            prompt=user_input,
            enable_thinking=False,
            req_id=req_id,
            cancel_event=cancel_event,
            timeout=timeout,
        )



def _validate_intent(intent: dict, user_input: str) -> dict:
    """
    ✅ 仅做枚举校验和非法值降级。
       不设置任何默认值——默认值统一由 recognize_intent 在 _validate_intent 之后处理。
       职责分离：本函数只管"纠偏"，不管"兜底"。

    ✅ 返回字典副本（浅拷贝），避免原地修改入参带来的副作用，
       保证上游仍可访问原始 LLM 返回值用于审计或重试判断。
    """
    validated = dict(intent)  # 浅拷贝，值是字符串，无需深拷贝

    if validated.get("category") not in VALID_INTENT_CATEGORIES:
        logger.warning(
            "[Intent] 非法 category=%s, 降级为 other",
            validated.get("category"),
        )
        validated["category"] = "other"

    if validated.get("complexity") not in VALID_INTENT_COMPLEXITIES:
        logger.warning(
            "[Intent] 非法 complexity=%s, 降级为 medium",
            validated.get("complexity"),
        )
        validated["complexity"] = "medium"

    # ✅ 不在此处设置任何默认值（如 needs_planning、summary 等）
    #    默认值统一在 recognize_intent 中处理
    return validated



async def recognize_intent(
    user_input: str,
    req_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict[str, Any]:
    """意图识别异步主入口"""
    # 空输入短路
    if not user_input or not user_input.strip():
        return {
            "needs_planning": False,
            "category": "other",
            "summary": "用户输入为空",
            "complexity": "simple",
        }

    # 正则硬拦截（同步）
    for pattern in SKIP_PLANNING_PATTERNS:
        if pattern.search(user_input):
            logger.info("[Intent] 显式拒绝规划命中 (req=%s): %s", req_id, pattern.pattern)
            return {
                "needs_planning": False,
                "category": "consultation",
                "summary": "用户显式拒绝规划",
                "complexity": "simple",
            }

    if USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        return _mock_intent(req_id)

    summary = "意图识别异常"
    try:
        prompt = _render_template(INTENT_PROMPT, req_id, user_input=user_input)
        raw = await _async_call_llm(
            model=LLM_INTENT_MODEL,
            prompt=prompt,
            enable_thinking=LLM_INTENT_ENABLE_THINKING,
            req_id=req_id,
            cancel_event=cancel_event,
        )
        intent = _extract_json(raw)
        if not intent or not isinstance(intent, dict):
            raise LLMResponseError("JSON解析为空或非dict", req_id)

        intent = _validate_intent(intent, user_input)
        intent.setdefault("needs_planning", False)
        intent.setdefault("category", "other")
        intent.setdefault("summary", "任务解析完成")
        intent.setdefault("complexity", "simple")

        # Post-check 二次拦截
        if intent.get("needs_planning") is True:
            for pattern in SKIP_PLANNING_PATTERNS:
                if pattern.search(user_input):
                    logger.warning("[Intent] LLM误判planning=True，正则二次拦截 (req=%s)", req_id)
                    intent["needs_planning"] = False
                    intent["category"] = "consultation"
                    intent["summary"] = "用户显式拒绝规划(LLM纠偏)"
                    break
            intent = _validate_intent(intent, user_input)
        return intent

    except LLMCancelledError:
        logger.warning("[LLMClient] 意图识别被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[LLMClient] 意图识别超时 (req=%s)", req_id)
        summary = "意图识别超时"
    except LLMResponseError as e:
        logger.error("[LLMClient] 意图解析格式错误: %s (req=%s)", e, req_id)
        summary = "意图解析格式错误"
    except LLMClientError as e:
        logger.error("[LLMClient] 意图识别服务异常: %s (req=%s)", e, req_id)
        summary = "意图识别服务异常"
    except Exception as e:
        logger.exception("[LLMClient] 意图识别未知异常: %s (req=%s)", e, req_id)
        summary = "意图识别异常"

    return {
        "needs_planning": False,
        "category": "other",
        "summary": summary,
        "complexity": "simple",
    }

async def generate_plan(
    user_input: str,
    intent_info: dict[str, Any],
    req_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict[str, Any]:
    """异步生成执行计划"""
    _PLAN_REQUIRED_KEYS = ("task_name", "description", "nodes", "edges")

    if not isinstance(intent_info, dict):
        logger.error("[Planner] intent_info 类型非法: %s (req=%s)", type(intent_info).__name__, req_id)
        return {
            "task_name": "规划失败",
            "description": "intent_info 参数类型非法",
            "nodes": [],
            "edges": [],
        }

    if USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        return _mock_plan(req_id)

    error_msg = "规划生成异常"
    try:
        prompt = _render_template(
            PLANNER_PROMPT, req_id,
            user_input=user_input,
            category=intent_info.get("category", "other"),
            summary=intent_info.get("summary", ""),
        )
        raw = await _async_call_llm(
            model=LLM_PLANNER_MODEL, prompt=prompt,
            enable_thinking=LLM_PLANNER_ENABLE_THINKING,
            req_id=req_id, cancel_event=cancel_event,
        )
        plan = _extract_json(raw)
        if not plan or not isinstance(plan, dict):
            raise LLMResponseError("Plan JSON解析为空或非dict", req_id)

        plan.setdefault("task_name", "未命名任务")
        plan.setdefault("description", "")
        plan.setdefault("nodes", [])
        plan.setdefault("edges", [])
        return plan

    except LLMCancelledError:
        logger.warning("[Planner] 规划生成被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[Planner] 规划生成超时 (req=%s)", req_id)
        error_msg = "规划生成超时"
    except LLMResponseError as e:
        logger.error("[Planner] 规划解析格式错误: %s (req=%s)", e, req_id)
        error_msg = f"规划解析格式错误: {e}"
    except LLMClientError as e:
        logger.error("[Planner] 规划生成服务异常: %s (req=%s)", e, req_id)
        error_msg = f"规划生成服务异常: {e}"
    except Exception as e:
        logger.exception("[Planner] 规划生成未知异常: %s (req=%s)", e, req_id)
        error_msg = "规划生成异常"

    return {
        "task_name": "规划失败",
        "description": f"[规划失败] {error_msg}",
        "nodes": [],
        "edges": [],
    }

async def refine_node(
    node: dict[str, Any],
    user_input: str,
    category: str,
    req_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict[str, Any]:
    """异步细化单节点"""
    _REFINE_REQUIRED_KEYS = ("name", "details", "meta")

    def _fallback_result(error_msg: str, original_node: dict) -> dict[str, Any]:
        return {
            "name": original_node.get("name", "未命名节点"),
            "details": f"[细化失败] {error_msg}",
            "meta": {"refine_error": error_msg, "fallback": True},
        }

    if not isinstance(node, dict):
        logger.error("[Refine] node 类型非法: %s (req=%s)", type(node).__name__, req_id)
        return _fallback_result("node参数类型非法", {})

    if USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        return _mock_refine(req_id)

    try:
        prompt = _render_template(
            NODE_REFINE_PROMPT, req_id,
            node_json=json.dumps(node, ensure_ascii=False),
            user_input=user_input,
            category=category,
        )
        raw = await _async_call_llm(
            model=LLM_NODE_MODEL,
            prompt=prompt,
            enable_thinking=LLM_NODE_ENABLE_THINKING,
            req_id=req_id,
            timeout=LLM_NODE_TIMEOUT,
            cancel_event=cancel_event,
        )
        result = _extract_json(raw)
        result = _normalize_llm_output(result, expected_keys=list(_REFINE_REQUIRED_KEYS))
        for key in _REFINE_REQUIRED_KEYS:
            if key not in result or result[key] is None:
                logger.warning("[Refine] 归一化后仍缺少字段 '%s'，使用原始节点值兜底 (req=%s)", key, req_id)
                result[key] = node.get(key, "")
        return result

    except LLMCancelledError:
        logger.warning("[Refine] 节点细化被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[Refine] 节点细化超时 (req=%s)", req_id)
        return _fallback_result("节点细化超时", node)
    except LLMResponseError as e:
        logger.error("[Refine] 节点细化解析格式错误: %s (req=%s)", e, req_id)
        return _fallback_result("节点细化解析格式错误", node)
    except LLMClientError as e:
        logger.error("[Refine] 节点细化服务异常: %s (req=%s)", e, req_id)
        return _fallback_result("节点细化服务异常", node)
    except Exception as e:
        logger.exception("[Refine] 节点细化未知异常: %s (req=%s)", e, req_id)
        return _fallback_result("节点细化异常", node)

# 重构后的 llm_client.py
async def execute_node_llm(
    prompt: str,
    req_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> tuple[bool, str]:
    """执行节点 LLM 调用（只负责调用和解析 JSON）"""
    rid = req_id or "unknown"

    if USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", rid)
        return True, "✅ Mock执行完成"

    try:
        async with _get_semaphore():
            raw = await _async_call_llm(
                model=LLM_NODE_MODEL, prompt=prompt,
                enable_thinking=LLM_NODE_ENABLE_THINKING,
                req_id=rid, timeout=timeout or LLM_NODE_TIMEOUT,
                cancel_event=cancel_event,
            )

        parsed = _extract_json(raw)
        if not parsed:
            logger.warning(
                "[Executor] LLM返回无法解析为JSON (req=%s): %s",
                rid, raw[:200],
            )
            return False, f"⚠️ 执行结果解析失败，原始输出: {raw[:300]}"

        success = bool(parsed.get("success", False))
        detail_text = str(parsed.get("detail", "无详细描述"))

        icon = "✅" if success else "❌"
        logger.info("[Executor] %s 执行完成 (req=%s)", icon, rid)

        return success, detail_text

    except LLMCancelledError:
        logger.info("[Executor] 执行被取消 (req=%s)", rid)
        return False, "⏹️ 执行被取消"

    except LLMTimeoutError as e:
        logger.warning("[Executor] ⏰ %s (req=%s)", e, rid)
        return False, "⏰ 执行超时，请稍后重试"

    except LLMClientError as e:
        logger.error("[Executor] 💥 %s (req=%s)", e, rid)
        return False, f"💥 执行服务异常: {str(e)[:200]}"

    except Exception as e:
        logger.exception("[Executor] 💥 未知异常 (req=%s)", rid)
        return False, f"💥 执行异常: {type(e).__name__}: {str(e)[:200]}"

# ============================================================
#  Mock 函数（异步，直接返回）
# ============================================================

def _mock_intent(req_id: str) -> dict[str, Any]:
    return {"needs_planning": True, "category": "cooking",
            "summary": "Mock: 任务解析完成", "complexity": "medium"}


def _mock_plan(req_id: str) -> dict[str, Any]:
    return {
        "task_name": "Mock任务", "description": "Mock模式生成的计划",
        "nodes": [
            {"id": 1, "name": "准备食材", "details": "洗切西红柿和鸡蛋"},
            {"id": 2, "name": "炒鸡蛋", "details": "热油炒散鸡蛋盛出"},
            {"id": 3, "name": "炒西红柿", "details": "炒出汤汁加糖"},
            {"id": 4, "name": "合并出锅", "details": "倒入鸡蛋混合均匀"},
        ],
        "edges": [
            {"from": 1, "to": 2, "label": "食材就绪"},
            {"from": 2, "to": 3, "label": "鸡蛋炒好"},
            {"from": 3, "to": 4, "label": "西红柿出汁"},
        ],
    }


def _mock_refine(req_id: str) -> dict[str, Any]:
    return {
        "name": "Mock: 执行核心步骤",
        "details": "1. 准备工具\n2. 执行操作\n3. 验证结果",
        "meta": {
            "preconditions": ["前置条件已满足"],
            "postconditions": ["预期结果达成"],
            "retry_policy": "失败后重试最多3次",
        },
    }
