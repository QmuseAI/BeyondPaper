import json, sys, re
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))
t = [x.get("text", "") for x in d if isinstance(x, dict) and "Source element" in x.get("text", "")][-1]
s = t.rfind("<<<BP_FINDINGS_JSON>>>", 0, t.find("<<<END_BP_FINDINGS_JSON>>>") if "<<<END_BP_FINDINGS_JSON>>>" in t else len(t))
e = t.find("<<<END_BP_FINDINGS_JSON>>>", s)
assert s >= 0 and e > s, "no complete block"
block = t[s:e + len("<<<END_BP_FINDINGS_JSON>>>")]
json.loads(block[len("<<<BP_FINDINGS_JSON>>>"):-len("<<<END_BP_FINDINGS_JSON>>>")])
open(dst, "w", encoding="utf-8").write(block + "\n")
print(dst, len(block))
