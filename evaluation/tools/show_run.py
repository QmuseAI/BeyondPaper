import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
t = d if isinstance(d, dict) else {"findings": d}
print("counts", t.get("counts"), "| repro", (t.get("reproduction") or {}).get("counts"), "| mv", {k: (t.get("multiverse") or {}).get(k) for k in ("robustness", "baseline_holds", "n_ok")}, "| sev_changes", t.get("severity_changes"))
for c in t.get("claims", []):
    print(" ", c.get("id"), c.get("light"), str(c.get("text"))[:110])
sev = set(sys.argv[2:]) or {"red", "yellow"}
for f in t["findings"]:
    if f.get("severity") in sev:
        print(f"- {f.get('id')} {f.get('severity')[:1].upper()} {f.get('type')} [{f.get('source')}] hw={f.get('hand_written')} orig={f.get('severity_original')} {f.get('severity_reasons') or ''}\n    loc: {str(f.get('location'))[:140]}\n    ev: {' '.join(str(f.get('evidence')).split())[:420]}")
