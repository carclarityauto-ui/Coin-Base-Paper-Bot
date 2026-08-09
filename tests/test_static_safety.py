from pathlib import Path

text = Path("app/main.py").read_text()

assert "MAX_TRADES_PER_DAY" not in text
assert "Maximum daily entry count reached" not in text
assert 'VERSION = 4' in text
assert 'EXECUTION_MODE != "paper"' in text
assert 'cash became negative' in text
assert 'position exceeds hard cap' in text
assert 'MIN_SECONDS_BETWEEN_ENTRIES' in text
print("Static safety checks passed.")
