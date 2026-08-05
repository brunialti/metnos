"""compare_runs.py — diff two bench outputs query-by-query."""
import sys
import re
sys.path.insert(0, '/opt/metnos/tests/simulator')
import os
os.environ['BENCH_500'] = '1'
from run_simulation_v2 import TEST_QUERIES


def parse_tops(fn: str) -> tuple[dict, dict]:
    """Parse bench output → (top1_dict, top2_dict)."""
    tops, tops2 = {}, {}
    with open(fn) as f:
        cur_q = None; done = {1: False, 2: False}
        for ln in f:
            ln = ln.rstrip()
            m = re.match(r'^Q: (.+)$', ln)
            if m:
                cur_q = m.group(1).strip()
                done = {1: False, 2: False}
                continue
            if cur_q:
                for n, dst in [(1, tops), (2, tops2)]:
                    if done[n]:
                        continue
                    m = re.match(rf'    {n}\. \[-?[\d\.]+\] (\S+)', ln)
                    if m:
                        dst[cur_q] = m.group(1)
                        done[n] = True
                        break
    return tops, tops2


def compare(file_a: str, file_b: str, label_a: str = "A", label_b: str = "B"):
    a_t1, a_t2 = parse_tops(file_a)
    b_t1, b_t2 = parse_tops(file_b)
    a_pass = a_fail = 0
    b_pass = b_fail = 0
    only_a = only_b = both_pass = both_fail = 0
    flipped_pos = []  # A fail → B pass
    flipped_neg = []  # A pass → B fail
    for t in TEST_QUERIES:
        q = t['query']
        a1, a2 = a_t1.get(q, '?'), a_t2.get(q, '?')
        b1, b2 = b_t1.get(q, '?'), b_t2.get(q, '?')
        ok_a = a1 in t['accepted_first'] or a2 in t['accepted_first']
        ok_b = b1 in t['accepted_first'] or b2 in t['accepted_first']
        if ok_a:
            a_pass += 1
        else:
            a_fail += 1
        if ok_b:
            b_pass += 1
        else:
            b_fail += 1
        if ok_a and not ok_b:
            flipped_neg.append((q, a1, b1))
            only_a += 1
        if ok_b and not ok_a:
            flipped_pos.append((q, a1, b1))
            only_b += 1
        if ok_a and ok_b:
            both_pass += 1
        if not ok_a and not ok_b:
            both_fail += 1
    n = len(TEST_QUERIES)
    print(f"=== Comparison {label_a} vs {label_b} ===")
    print(f"{label_a}: {a_pass}/{n} = {100*a_pass/n:.1f}% top-2 pass")
    print(f"{label_b}: {b_pass}/{n} = {100*b_pass/n:.1f}% top-2 pass")
    print(f"Both pass: {both_pass} | Both fail: {both_fail}")
    print(f"Only {label_a} pass (B regressed): {only_a}")
    print(f"Only {label_b} pass (B improved): {only_b}")
    print()
    print(f"=== {label_b} improved on (showing 10): ===")
    for q, a, b in flipped_pos[:10]:
        print(f"  ✓ {q[:60]:<61} {label_a}={a:<22} {label_b}={b}")
    print()
    print(f"=== {label_b} regressed on (showing 10): ===")
    for q, a, b in flipped_neg[:10]:
        print(f"  ✗ {q[:60]:<61} {label_a}={a:<22} {label_b}={b}")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "/tmp/iter_500_honest15.out"
    b = sys.argv[2] if len(sys.argv) > 2 else "/tmp/iter_500_qwen35.out"
    label_a = sys.argv[3] if len(sys.argv) > 3 else "Gemma26B"
    label_b = sys.argv[4] if len(sys.argv) > 4 else "Qwen3.5-9B"
    compare(a, b, label_a, label_b)
