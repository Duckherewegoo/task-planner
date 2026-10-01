from pathlib import Path

root = Path("data/blocked_words")
single = []
very_short = []
for p in root.glob("*.txt"):
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = p.read_text(encoding="gbk")
    for line in text.splitlines():
        w = line.strip()
        if not w or w.startswith("#"):
            continue
        if len(w) == 1:
            single.append((p.name, w))
        elif len(w) == 2:
            very_short.append((p.name, w))

print(f"单字词: {len(single)}")
for f, w in single[:30]:
    print(f"  [{f}] {w}")
print(f"\n双字词: {len(very_short)}")
for f, w in very_short[:20]:
    print(f"  [{f}] {w}")
