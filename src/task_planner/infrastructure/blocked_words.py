"""
blocked_words.py — 违禁词库加载器。
递归扫描词库目录下所有 .txt 文件，每行一个词。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import FrozenSet, Iterator, List, Optional, Set

logger = logging.getLogger(__name__)

_DEFAULT_WORDS_DIR = Path(__file__).resolve().parent / "docs" / "blocked_words"
_IGNORE_DIR_NAMES = {".git", "__pycache__", ".idea", ".vscode", ".venv", "venv"}


def load_blocked_words(
    root: Optional[Path] = None,
    *,
    encoding: str = "utf-8",
    strict: bool = False,
) -> FrozenSet[str]:
    root = Path(root) if root else _DEFAULT_WORDS_DIR

    if not root.exists():
        logger.warning("违禁词库目录不存在，BLOCKED_WORDS 将为空: %s", root)
        return frozenset()
    if not root.is_dir():
        logger.error("违禁词库路径不是目录: %s", root)
        return frozenset()

    words: Set[str] = set()
    file_count = 0

    for txt_file in _iter_txt_files(root):
        try:
            file_words = _read_words_from_file(txt_file, encoding)
            words.update(file_words)
            file_count += 1
            logger.debug("已加载 %d 词 <- %s", len(file_words), txt_file.relative_to(root))
        except Exception as e:
            if strict:
                raise
            logger.warning("读取词库文件失败，已跳过: %s (%s)", txt_file, e)

    logger.info(
        "违禁词库加载完成: %d 个文件，去重后 %d 个词 (root=%s)",
        file_count, len(words), root,
    )
    return frozenset(words)


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


def _read_words_from_file(path: Path, encoding: str) -> List[str]:
    encodings = [encoding]
    if encoding.lower().replace("-", "") == "utf8":
        encodings.append("utf-8-sig")

    text: Optional[str] = None
    last_err: Optional[Exception] = None
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue

    if text is None:
        raise last_err or RuntimeError(f"无法解码文件: {path}")

    return [
        w for w in (line.strip() for line in text.splitlines())
        if w and not w.startswith("#")
    ]

# ═══════════════════════════════════════════════════════════════════
#  惰性模块级属性 —— 兼容旧 config.BLOCKED_WORDS 的引用
# ═══════════════════════════════════════════════════════════════════
_BLOCKED_WORDS_CACHE: Optional[FrozenSet[str]] = None


def _get_blocked_words() -> FrozenSet[str]:
    """首次访问时才真正加载词库（scan 盘）"""
    global _BLOCKED_WORDS_CACHE
    if _BLOCKED_WORDS_CACHE is None:
        _BLOCKED_WORDS_CACHE = load_blocked_words()
    return _BLOCKED_WORDS_CACHE


def __getattr__(name: str):
    """PEP 562：模块级属性访问钩子"""
    if name == "BLOCKED_WORDS":
        return _get_blocked_words()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
