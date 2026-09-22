import re

CHANNELS = ("ALPHA", "BETA", "RC")
_CHANNEL_RANK = {name: i for i, name in enumerate(CHANNELS)}
RELEASE_RANK = len(CHANNELS)

_NUMS = re.compile(r"\d+")
_PRE = re.compile(r"(?P<channel>" + "|".join(CHANNELS) + r")[ _.\-]*(?P<num>[\d.]*)", re.I)
_DOTTED = re.compile(r"\d+(?:\.\d+)*")
_RELEASE_PARTS = 3


def _pad(nums):
    nums = tuple(nums)[:_RELEASE_PARTS]
    return nums + (0,) * (_RELEASE_PARTS - len(nums))


def parse(text):
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    if not s:
        return None
    m = _PRE.search(s)
    head = s if m is None else s[:m.start()]
    release = _pad(int(n) for n in _NUMS.findall(head))
    if m is None:
        if not _DOTTED.fullmatch(s):
            return None
        return (release, RELEASE_RANK, ())
    rank = _CHANNEL_RANK[m.group("channel").upper()]
    pre = tuple(int(n) for n in _NUMS.findall(m.group("num")))
    return (release, rank, pre or (0,))


def is_newer(candidate, current):
    a, b = parse(candidate), parse(current)
    if a is None or b is None:
        return False
    return a > b


_CHANNEL_STRIDE = 1000


def file_version(text):
    v = parse(text)
    if v is None:
        return (0, 0, 0, 0)
    release, rank, pre = v
    tail = rank * _CHANNEL_STRIDE + min(pre[0] if pre else 0, _CHANNEL_STRIDE - 1)
    return (release[0], release[1], release[2], tail)
