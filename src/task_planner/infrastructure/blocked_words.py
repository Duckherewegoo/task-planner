"""
blocked_words.py — 违禁词库加载与匹配
═══════════════════════════════════════════════════════════════════════
策略：
  1. Aho-Corasick 精确子串匹配（O(n)，与词库规模无关）
  2. 单字/单数字/单字母过滤（_MIN_WORD_LEN = 2）
  3. 白名单子串豁免（避免误杀 "analyze" 里的 "anal"）
  4. 分级处置：block（拦截） / review（待审）

目录约定：
  <project_root>/data/blocked_words/   ← 词库（可嵌套）
  <project_root>/data/whitelist.txt    ← 白名单

环境变量（可选）：
  BLOCKED_WORDS_DIR         覆盖词库目录
  BLOCKED_WORDS_WHITELIST   覆盖白名单路径
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

logger = logging.getLogger(__name__)

# ── 路径 ──
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_WORDS_DIR = Path(
    os.getenv("BLOCKED_WORDS_DIR", str(_PROJECT_ROOT / "data" / "blocked_words"))
)
_DEFAULT_WHITELIST = Path(
    os.getenv("BLOCKED_WORDS_WHITELIST", str(_PROJECT_ROOT / "data" / "whitelist.txt"))
)

# ── 过滤规则 ──
_MIN_WORD_LEN = 2      # 单字/单数字/单字母一律过滤
_MAX_WORD_LEN = 50     # 超长多半是 URL 或误录

# ── 停用词：明确不作为违禁词的高频常用词 ──
# 这层是"最后一道保险"：即使某词库里误录了，也不会进匹配器
_COMMON_WORDS: FrozenSet[str] = frozenset({
    # 代词
    "一个", "这个", "那个", "这些", "那些", "什么", "怎么", "怎样",
    "我们", "你们", "他们", "她们", "它们",
    # 连词/副词
    "因为", "所以", "但是", "不过", "而且", "就是", "如果", "虽然",
    "可以", "应该", "可能", "也许", "一定", "必须",
    # 助词/常用语
    "的话", "一下", "进行", "通过", "关于", "对于", "由于", "为了",
    "需要", "想要", "希望", "谢谢", "请问", "麻烦",
})

# ── 分级：文件名 → tier ──
# 说明：明确列出的进 block（拦截），其余默认 review（标记待审）
_DEFAULT_TIER = "review"
_FILE_TIER_MAP: Dict[str, str] = {
    # —— Block：强违禁，直接拦截 ——
    "反动词库.txt": "block",
    "暴恐词库.txt": "block",
    "色情词库.txt": "block",
    "涉枪涉爆.txt": "block",

    # —— Review：默认待审 ——
    "COVID-19词库.txt": "review",
    "GFW补充词库.txt": "review",
    "其他词库.txt": "review",
    "广告类型.txt": "review",
    "政治类型.txt": "review",
    "新思想启蒙.txt": "review",
    "民生词库.txt": "review",
    "网易前端过滤敏感词库.txt": "review",
    "色情类型.txt": "review",
    "补充词库.txt": "review",
    "贪腐词库.txt": "review",
    "零时-Tencent.txt": "review",
    "非法网址.txt": "review",
}

# 忽略的目录（不递归进去）
_IGNORE_DIR_NAMES = {".git", "__pycache__", ".idea", ".vscode", ".venv", "venv"}

# ── AC自动机（可选依赖） ──
try:
    import ahocorasick  # type: ignore
    _HAS_AHO = True
except ImportError:
    _HAS_AHO = False
    logger.warning(
        "pyahocorasick 未安装，将退化为朴素子串匹配。"
        "安装命令: pip install pyahocorasick"
    )


# ═══════════════════════════════════════════════════════════════════════
#  匹配器
# ═══════════════════════════════════════════════════════════════════════
class BlockedWordMatcher:
    """
    违禁词匹配器：
      - 精确子串匹配（AC自动机 or 朴素回退）
      - 白名单子串豁免
      - block / review 分级
    """

    def __init__(
        self,
        block_words: Set[str],
        review_words: Set[str],
        whitelist: Set[str],
    ) -> None:
        self.block_words: Set[str] = set(block_words)
        self.review_words: Set[str] = set(review_words) - self.block_words
        self.whitelist: Set[str] = set(whitelist)
        self.all_words: Set[str] = self.block_words | self.review_words
        self._aho = None
        self._build()

    # ---- 构建 ----
    def _build(self) -> None:
        if _HAS_AHO and self.all_words:
            self._aho = ahocorasick.Automaton()
            for w in self.all_words:
                self._aho.add_word(w, w)
            self._aho.make_automaton()

    # ---- 对外 API ----
    def match(self, text: str) -> Dict[str, List[str]]:
        """
        返回：
          {
            "blocked":     [...],   # 命中 block 级（决定是否拒绝）
            "review":      [...],   # 命中 review 级（标记待审）
            "whitelisted": [...],   # 被白名单豁免的命中（仅用于调试）
          }
        """
        if not text:
            return {"blocked": [], "review": [], "whitelisted": []}

        blocked: Set[str] = set()
        review: Set[str] = set()
        whitelisted: Set[str] = set()

        for word, start, end in self._iter_hits(text):
            if self._is_whitelisted(text, start, end):
                whitelisted.add(word)
                continue
            if word in self.block_words:
                blocked.add(word)
            else:
                review.add(word)

        return {
            "blocked": sorted(blocked),
            "review": sorted(review),
            "whitelisted": sorted(whitelisted),
        }

    def is_blocked(self, text: str) -> bool:
        """是否含 block 级违禁词"""
        return bool(self.match(text)["blocked"])

    # ---- 内部 ----
    def _iter_hits(self, text: str) -> Iterator[Tuple[str, int, int]]:
        """产出 (word, start, end)，end 为最后一个字符的下标"""
        if self._aho is not None:
            for end_idx, word in self._aho.iter(text):
                start = end_idx - len(word) + 1
                yield word, start, end_idx
        else:
            # 朴素回退：长词优先，避免短词遮蔽长词
            for w in sorted(self.all_words, key=len, reverse=True):
                start = text.find(w)
                while start != -1:
                    yield w, start, start + len(w) - 1
                    start = text.find(w, start + 1)

    def _is_whitelisted(self, text: str, start: int, end: int) -> bool:
        """
        命中区间 [start, end] 是否完全落在某个白名单词内部。
        注意：与白名单词完全相同 → 不豁免（否则白名单等于后门）。
        """
        if not self.whitelist:
            return False
        for w in self.whitelist:
            idx = text.find(w)
            while idx != -1:
                w_start = idx
                w_end = idx + len(w) - 1
                if w_start <= start and end <= w_end:
                    if not (w_start == start and w_end == end):
                        return True
                idx = text.find(w, idx + 1)
        return False

    def __repr__(self) -> str:
        return (
            f"<BlockedWordMatcher "
            f"block={len(self.block_words)} "
            f"review={len(self.review_words)} "
            f"whitelist={len(self.whitelist)} "
            f"aho={'on' if self._aho else 'off'}>"
        )


# ═══════════════════════════════════════════════════════════════════════
#  加载
# ═══════════════════════════════════════════════════════════════════════
def _iter_txt_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts[:-1]
        if any(p in _IGNORE_DIR_NAMES or p.startswith(".") for p in rel_parts):
            continue
        if path.suffix.lower() != ".txt":
            continue
        yield path


def _read_text(path: Path, encoding: str = "utf-8") -> str:
    """读文本，自动 fallback utf-8-sig 兼容 BOM"""
    encodings = [encoding]
    if encoding.lower().replace("-", "") == "utf8":
        encodings.append("utf-8-sig")
    last_err: Optional[Exception] = None
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise last_err or RuntimeError(f"无法解码: {path}")


def _parse_words(text: str) -> List[str]:
    """一行一个词；跳过空行 / # 注释 / 长度越界"""
    out: List[str] = []
    for line in text.splitlines():
        w = line.strip()
        if not w or w.startswith("#"):
            continue
        if len(w) < _MIN_WORD_LEN or len(w) > _MAX_WORD_LEN:
            continue
        if w in _COMMON_WORDS:          # ← 新增
            continue
        out.append(w)
    return out


def _load_words_by_tier(root: Path) -> Tuple[Set[str], Set[str]]:
    """返回 (block_words, review_words)"""
    block: Set[str] = set()
    review: Set[str] = set()
    skipped_common: Set[str] = set()

    if not root.exists():
        logger.warning("词库目录不存在: %s", root)
        return block, review

    file_count = 0
    for txt in _iter_txt_files(root):
        tier = _FILE_TIER_MAP.get(txt.name, _DEFAULT_TIER)
        try:
            text = _read_text(txt)
        except Exception as e:
            logger.warning("读取失败: %s (%s)", txt, e)
            continue

        words = _parse_words(text)
        clean: List[str] = []
        for w in words:
            if w in _COMMON_WORDS:
                skipped_common.add(w)
                continue
            clean.append(w)

        (block if tier == "block" else review).update(clean)
        file_count += 1
        logger.debug("[%s] %s → %d 词 (跳过 %d)",
                     tier, txt.name, len(clean), len(words) - len(clean))

    review -= block
    logger.info(
        "词库加载完成: %d 文件, block=%d, review=%d, 跳过停用词 %d 个: %s",
        file_count, len(block), len(review),
        len(skipped_common), sorted(skipped_common)[:10],
    )
    return block, review
def _load_whitelist(path: Path) -> Set[str]:
    if not path.exists():
        logger.info("白名单不存在，跳过: %s", path)
        return set()
    try:
        text = _read_text(path)
    except Exception as e:
        logger.warning("白名单读取失败: %s (%s)", path, e)
        return set()
    words = set(_parse_words(text))
    logger.info("白名单加载完成: %d 词", len(words))
    return words


# ═══════════════════════════════════════════════════════════════════════
#  全局单例（惰性）
# ═══════════════════════════════════════════════════════════════════════
_MATCHER_CACHE: Optional[BlockedWordMatcher] = None


def get_matcher() -> BlockedWordMatcher:
    """获取全局匹配器（首次调用时构建）"""
    global _MATCHER_CACHE
    if _MATCHER_CACHE is None:
        block, review = _load_words_by_tier(_DEFAULT_WORDS_DIR)
        whitelist = _load_whitelist(_DEFAULT_WHITELIST)
        _MATCHER_CACHE = BlockedWordMatcher(block, review, whitelist)
        logger.info("匹配器就绪: %r", _MATCHER_CACHE)
    return _MATCHER_CACHE


def reload_matcher() -> BlockedWordMatcher:
    """重新加载（测试 / 热更新用）"""
    global _MATCHER_CACHE
    _MATCHER_CACHE = None
    return get_matcher()


# ═══════════════════════════════════════════════════════════════════════
#  对外便捷 API
# ═══════════════════════════════════════════════════════════════════════
def check_text(text: str) -> Dict[str, List[str]]:
    """检查文本，返回 {'blocked': [...], 'review': [...], 'whitelisted': [...]}"""
    return get_matcher().match(text)


def is_blocked(text: str) -> bool:
    """是否含 block 级违禁词（用于快速判定是否拒绝）"""
    return get_matcher().is_blocked(text)


# ═══════════════════════════════════════════════════════════════════════
#  模块级惰性属性（兼容旧 config.BLOCKED_WORDS 的引用）
# ═══════════════════════════════════════════════════════════════════════
def __getattr__(name: str):
    """
    PEP 562：`from blocked_words import BLOCKED_WORDS` 会触发首次加载。
    不直接定义模块级变量，避免 import 时立即扫盘。
    """
    if name == "BLOCKED_WORDS":      # 全部词（block + review）
        m = get_matcher()
        return frozenset(m.all_words)
    if name == "BLOCK_WORDS":        # 仅 block 级
        return frozenset(get_matcher().block_words)
    if name == "REVIEW_WORDS":       # 仅 review 级
        return frozenset(get_matcher().review_words)
    if name == "WHITELIST":
        return frozenset(get_matcher().whitelist)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ═══════════════════════════════════════════════════════════════════════
#  兼容旧 API：load_blocked_words()
# ═══════════════════════════════════════════════════════════════════════
def load_blocked_words(
    root: Optional[Path] = None,
    *,
    encoding: str = "utf-8",
    strict: bool = False,
) -> FrozenSet[str]:
    """
    兼容旧接口：加载词库返回去重后的 frozenset。
    不传 root → 用全局单例；传 root → 现场加载（用于测试）。
    """
    if root is None:
        return frozenset(get_matcher().all_words)

    root = Path(root)
    if not root.exists():
        logger.warning("词库目录不存在: %s", root)
        return frozenset()

    words: Set[str] = set()
    for txt in _iter_txt_files(root):
        try:
            text = _read_text(txt, encoding)
            words.update(_parse_words(text))
        except Exception as e:
            if strict:
                raise
            logger.warning("读取失败: %s (%s)", txt, e)
    return frozenset(words)
