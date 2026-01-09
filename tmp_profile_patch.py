from pathlib import Path
text = Path('bot/handlers/profile.py').read_text(encoding='cp1251')
start = text.index('text = (')
print(text[start:start+200].encode('unicode_escape'))

