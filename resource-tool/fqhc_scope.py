import json, time, resource_sources as rs, match_audit as ma, batch_fqhc as B
master = json.load(open('audit_results/inventory_master.json',encoding='utf-8'))
try:
    prog = json.loads(open('audit_results/fqhc_progress.json',encoding='utf-8').read())
except Exception:
    prog = {"done":[], "handled":[]}
done=set(prog.get("done",[])); handled=set(prog.get("handled",[]))
total_remaining=0; rows=[]
for st in B.STATES:
    try:
        src = rs.query_fqhc_by_state(st)
    except Exception as e:
        rows.append((st, -1)); continue
    gaps=[g for g in ma.find_gaps(master, src)
          if g.get('name') and g.get('address1') and g.get('city') and g.get('state')
          and g['name'] not in handled]
    rows.append((st, 0 if st in done else len(gaps)))
    if st not in done: total_remaining += len(gaps)
    time.sleep(0.3)
rows.sort(key=lambda r:-r[1])
print(f"TOTAL FQHCs left to add nationwide: {total_remaining}")
print(f"States complete: {len(done)}/{len(B.STATES)}")
print("Biggest remaining states:")
for st,n in rows[:12]:
    if n>0: print(f"  {st}: {n}")
