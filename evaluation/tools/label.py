import csv,sys,json
run=sys.argv[1]; labels=json.loads(sys.argv[2])
p=f"results/{run}/adjudication.csv"
rows=list(csv.DictReader(open(p,encoding="utf-8-sig"))); fn=list(rows[0].keys())
for r in rows:
    if r["finding_id"] in labels:
        r["human_label"],r["note"]=labels[r["finding_id"]]
w=csv.DictWriter(open(p,"w",encoding="utf-8-sig",newline=""),fieldnames=fn); w.writeheader(); w.writerows(rows)
print(sum(1 for r in rows if r["human_label"]),"labelled of",len(rows))
