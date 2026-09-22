import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import version as V

ORDER = [
    "ALPHA_1", "ALPHA_2", "ALPHA_3", "ALPHA_10",
    "BETA_1", "BETA_2", "BETA_10",
    "RC_1", "RC_2",
    "0.9.0", "0.9.1", "0.10.0",
    "1.0.0-ALPHA_1", "1.0.0-BETA_1", "1.0.0-RC_1", "1.0.0",
    "1.0.1", "1.1.0", "2.0.0",
]

SAME = [
    ("ALPHA_3", "vALPHA_3"), ("ALPHA_3", "alpha_3"), ("ALPHA_3", "ALPHA-3"),
    ("ALPHA_3", "ALPHA 3"), ("ALPHA_3", "ALPHA3"),
    ("1.0.0", "v1.0.0"), ("BETA_1", "beta.1"),
]

UNREADABLE = ["", "   ", "nightly", "ward9", None, 3, "v"]


def main():
    fails = []
    print("порядок версий (%d штук), проверяю все пары:" % len(ORDER))
    for i, lo in enumerate(ORDER):
        for hi in ORDER[i + 1:]:
            if not V.is_newer(hi, lo):
                fails.append("%s должна быть новее %s" % (hi, lo))
            if V.is_newer(lo, hi):
                fails.append("%s НЕ должна быть новее %s" % (lo, hi))
    for a in ORDER:
        if V.is_newer(a, a):
            fails.append("%s новее самой себя" % a)
    print("   пар проверено: %d" % (len(ORDER) * (len(ORDER) - 1) // 2))

    print("одна и та же версия, записанная по-разному:")
    for a, b in SAME:
        same = V.parse(a) == V.parse(b)
        print("   %-10s == %-12s %s" % (a, b, "да" if same else "НЕТ"))
        if not same:
            fails.append("%s и %s читаются по-разному" % (a, b))
        if V.is_newer(b, a) or V.is_newer(a, b):
            fails.append("%s и %s считаются разными версиями" % (a, b))

    print("нечитаемое не считается обновлением:")
    for bad in UNREADABLE:
        if V.parse(bad) is not None:
            fails.append("%r прочиталось, хотя не должно" % (bad,))
        if V.is_newer(bad, "ALPHA_2") or V.is_newer("ALPHA_2", bad):
            fails.append("%r участвует в сравнении" % (bad,))
    print("   проверено: %d" % len(UNREADABLE))

    print("четыре числа для Windows растут в том же порядке:")
    prev, prev_name = None, None
    for name in ORDER:
        fv = V.file_version(name)
        if prev is not None and not fv > prev:
            fails.append("file_version %s %s не больше %s %s" % (name, fv, prev_name, prev))
        prev, prev_name = fv, name
    print("   %s -> %s ... %s -> %s" % (ORDER[0], V.file_version(ORDER[0]),
                                        ORDER[-1], V.file_version(ORDER[-1])))

    print("текущая версия игры:")
    from game import settings as S
    print("   settings.VERSION = %r -> %s, для Windows %s"
          % (S.VERSION, V.parse(S.VERSION), V.file_version(S.VERSION)))
    if V.parse(S.VERSION) is None:
        fails.append("settings.VERSION не читается")

    if fails:
        print("\nFAIL: %d" % len(fails))
        for f in fails[:20]:
            print("   " + f)
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
