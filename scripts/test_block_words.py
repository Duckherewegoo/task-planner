#!/usr/bin/env python3
"""
test_block_words.py — 违禁词库加载 / 匹配 / 白名单 / 性能 一站式测试
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 让脚本能直接跑（不用先 pip install -e .）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from task_planner.infrastructure.blocked_words import (BLOCK_WORDS,
                                                       BLOCKED_WORDS,
                                                       REVIEW_WORDS, WHITELIST,
                                                       check_text, get_matcher,
                                                       is_blocked,
                                                       reload_matcher)


def hr(title: str) -> None:
    print()
    print("═" * 68)
    print(title)
    print("═" * 68)


# ── 1. 加载统计 ──
def test_loading() -> None:
    hr("1. 加载统计")
    m = get_matcher()
    print(f"  block 级词:  {len(m.block_words):>7}")
    print(f"  review 级词: {len(m.review_words):>7}")
    print(f"  白名单:      {len(m.whitelist):>7}")
    print(f"  合计参与匹配: {len(m.all_words):>7}")
    print(f"  AC 自动机:   {'✅ 已启用' if m._aho else '⚠️  未安装 pyahocorasick'}")


# ── 2. 单字过滤验证 ──
def test_single_char_filter() -> None:
    hr("2. 单字/单数字/单字母过滤")
    all_words = BLOCKED_WORDS
    singles = [w for w in all_words if len(w) == 1]
    print(f"  单字词数量: {len(singles)}  (期望 0)")
    if singles:
        print(f"  ❌ 前 10 个: {singles[:10]}")
    else:
        print(f"  ✅ 全部过滤干净")


# ── 3. 基础匹配 ──
def test_basic_match() -> None:
    hr("3. 基础匹配")
    cases = [
        "帮我搭建一个 K8s 集群，要生产级配置",
        "分析一下这个算法的复杂度",
        "今天天气不错",
        "这个方案应该可以",           # ← 新增，验证"一个""这个""可以"都被过滤
    ]
    for text in cases:
        r = check_text(text)
        status = "❌ BLOCK" if r["blocked"] else ("⚠️  REVIEW" if r["review"] else "✅ PASS")
        print(f"  [{status}] {text!r}")
        if r["blocked"]:
            print(f"      blocked = {r['blocked'][:5]}")
        if r["review"]:
            print(f"      review  = {r['review'][:5]}")

# ── 4. 白名单豁免 ──
def test_whitelist() -> None:
    hr("4. 白名单豁免")
    m = get_matcher()
    if not m.whitelist:
        print("  ⚠️  白名单为空（data/whitelist.txt 里没放词），跳过")
        print("  提示：加一个词，比如 analyze，然后重跑")
        return

    for w in list(m.whitelist)[:5]:
        text = f"我们要 {w} 一下"
        r = check_text(text)
        if r["whitelisted"]:
            print(f"  ✅ '{w}' 生效，豁免了: {r['whitelisted']}")
        else:
            print(f"  ⚠️  '{w}' 在文本中未触发豁免（该文本里没有敏感子串）")


# ── 5. 自我命中测试（白名单不能等于敏感词本身） ──
def test_whitelist_edge() -> None:
    hr("5. 白名单边界：白名单词等于敏感词本身，不应豁免")
    m = get_matcher()
    # 随便找个 block 词做测试
    if not m.block_words:
        print("  ⚠️  无 block 词，跳过")
        return
    sample = next(iter(m.block_words))
    from task_planner.infrastructure.blocked_words import BlockedWordMatcher
    mm = BlockedWordMatcher(
        block_words={sample},
        review_words=set(),
        whitelist={sample},   # 白名单 = 敏感词本身
    )
    r = mm.match(f"这里有{sample}出现")
    if r["blocked"]:
        print(f"  ✅ 正确拦截（未被白名单后门豁免）: {sample!r}")
    else:
        print(f"  ❌ 被后门豁免了！: {sample!r}")


# ── 6. 性能 ──
def test_performance() -> None:
    hr("6. 性能")
    m = get_matcher()
    text = "帮我搭建一个 K8s 集群，需要配置网络和存储，以及监控告警。" * 100
    print(f"  文本长度: {len(text)} 字符")

    # 预热
    for _ in range(5):
        m.match(text)

    # 计时
    iterations = 100
    t0 = time.perf_counter()
    for _ in range(iterations):
        m.match(text)
    dt = (time.perf_counter() - t0) / iterations
    print(f"  单次匹配: {dt * 1000:.2f} ms")

    if dt < 0.005:
        print("  ✅ 极快（AC 自动机）")
    elif dt < 0.05:
        print("  ✅ 良好")
    elif dt < 0.2:
        print("  ⚠️  一般，建议装 pyahocorasick")
    else:
        print("  ❌ 慢，请装 pyahocorasick")


# ── 7. 便捷 API 验证 ──
def test_convenience_api() -> None:
    hr("7. 便捷 API")
    print(f"  is_blocked('正常文本'): {is_blocked('正常文本')}")
    print(f"  is_blocked('正常文本' * 100): {is_blocked('正常文本' * 100)}")


# ── 8. 热重载 ──
def test_reload() -> None:
    hr("8. 热重载")
    m1 = get_matcher()
    m2 = reload_matcher()
    print(f"  reload 前: {m1!r}")
    print(f"  reload 后: {m2!r}")
    print("  ✅ 重载完成")


def main() -> None:
    print()
    print("╔" + "═" * 66 + "╗")
    print("║" + " " * 20 + "违禁词库测试套件" + " " * 30 + "║")
    print("╚" + "═" * 66 + "╝")

    test_loading()
    test_single_char_filter()
    test_basic_match()
    test_whitelist()
    test_whitelist_edge()
    test_performance()
    test_convenience_api()
    test_reload()

    print()
    print("═" * 68)
    print("✅ 全部测试完成")
    print("═" * 68)


if __name__ == "__main__":
    main()
