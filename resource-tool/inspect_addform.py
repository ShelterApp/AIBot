"""
inspect_addform.py — Deep diagnostic of the Add Service form. Dumps every
input/textarea/select (with resolved label, placeholder, aria-label, type,
value) plus all buttons and role=button elements, so we can see exactly how
each field (esp. SERVICE DETAILS) and the submit button are represented.

Writes audit_results/addform_inspect.json.

    py inspect_addform.py --add-url "https://shelter.app/add_service"
"""
import json, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

JS = r"""
() => {
  function getLabelText(inp) {
    let t = inp.getAttribute('aria-label'); if (t) return t.trim();
    if (inp.id) { const l = document.querySelector('label[for="'+inp.id+'"]'); if (l) return l.textContent.trim(); }
    const pl = inp.closest('label');
    if (pl) return Array.from(pl.childNodes).filter(n=>n!==inp&&!n.contains(inp)).map(n=>n.textContent.trim()).filter(Boolean).join(' ');
    let el = inp;
    for (let d=0; d<4; d++){ const p=el.parentElement; if(!p)break; const sib=Array.from(p.childNodes); const i=sib.indexOf(el);
      for(let j=i-1;j>=0;j--){ const s=sib[j];
        if(s.nodeType===1){ const x=s.textContent.trim(); if(x&&x.length<100&&!s.querySelector('input,textarea,select')) return x; }
        else if(s.nodeType===3){ const x=s.textContent.trim(); if(x) return x; } }
      el=p; }
    return '';
  }
  const short = e => (e.className && e.className.toString) ? e.className.toString().slice(0,50) : '';
  const fields = [];
  document.querySelectorAll('input, textarea, select').forEach(inp => {
    const r = inp.getBoundingClientRect();
    fields.push({ tag: inp.tagName, type: inp.type || '', id: inp.id || '',
      name: inp.getAttribute('name') || '', placeholder: inp.getAttribute('placeholder'),
      aria: inp.getAttribute('aria-label'), resolvedLabel: getLabelText(inp),
      value: (inp.value || '').slice(0,30), cls: short(inp), y: Math.round(r.top) });
  });
  const buttons = [];
  document.querySelectorAll("button, [role='button']").forEach(b => {
    const r = b.getBoundingClientRect();
    buttons.push({ tag: b.tagName, role: b.getAttribute('role'),
      text: (b.textContent||'').trim().slice(0,30), aria: b.getAttribute('aria-label'),
      cls: short(b), y: Math.round(r.top) });
  });
  return { fields, buttons };
}
"""

ap = argparse.ArgumentParser()
ap.add_argument("--add-url", default="https://shelter.app/add_service")
a = ap.parse_args()
with sync_playwright() as p:
    ud = str(Path.home() / ".shelterapp-audit-browser")
    ctx = p.chromium.launch_persistent_context(ud, headless=False, slow_mo=60,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(a.add_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print("note:", e)
    print("\nLog in if needed; make sure the Add Service form is showing. Then press ENTER.")
    input(">>> ")
    pages = ctx.pages; pg = pages[-1] if len(pages) > 1 else pg
    pg.wait_for_timeout(1000)
    data = pg.evaluate(JS)
    Path("audit_results").mkdir(exist_ok=True)
    Path("audit_results/addform_inspect.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"\nWrote audit_results/addform_inspect.json: {len(data['fields'])} fields, "
          f"{len(data['buttons'])} buttons")
    print("Closing..."); ctx.close()
