# ── 配置 ──
from task_planner.infrastructure.cog import hub

hub.dev.LLM_TIMEOUT  # → 120
hub.dev.LLM_TIMEOUT = 60  # 开发者
hub.user.THEME = "dark"  # 用户
hub.user.LLM_TIMEOUT = 60  # ❌ PermissionError

for e in hub.snapshot():
    print(e.section, e.name, "=", e.value, "|", e.purpose)

# ── 违禁词 ──
from task_planner.infrastructure.blocked_words import load_blocked_words

BLOCKED_WORDS = load_blocked_words()

# ── Prompts ──
from prompts.loader import INTENT_PROMPT

# ── Cytoscape ──
# ── 常量 ──
# ── 正则 ──
# ── UI 样式 ──

msg = INTENT_PROMPT.substitute(user_input="帮我搭个K8s集群")
