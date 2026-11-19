"""Very small stopword-based language detector for en/fr/pt/sw. Returns 'auto' when unsure.
This is a placeholder for a real detector (e.g. fastText lid) - good enough to stop hard-coding 'en'."""
import re

_SW = {
    "en": "the and of to in is for with on that by as are be this from or at an it not have which was can should".split(),
    "fr": "le la les des et un une du pour que est dans qui par sur au avec ne pas sont ce cette plus".split(),
    "pt": "de do da dos das em um uma para que com por no na nos nas ao mais ou ser foi não são pelo pela".split(),
    "sw": "na ya wa kwa ni katika za la cha hii huo kuwa mtoto watoto au kama pia lakini ili wake yake hadi sana".split(),
}


def detect_language(text: str, default="auto") -> str:
    toks = re.findall(r"[a-zà-ÿ]+", (text or "").lower())
    if len(toks) < 12:
        return default
    scores = {lang: sum(1 for t in toks if t in set(words)) / len(toks) for lang, words in _SW.items()}
    best = max(scores, key=scores.get)
    second = sorted(scores.values())[-2]
    if scores[best] < 0.10 or scores[best] - second < 0.04:
        return default
    return best
