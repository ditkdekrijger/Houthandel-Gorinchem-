"""Uitvoer: CSV (voor import in kassa/webshop) en een zelfstandig HTML-dagrapport."""
from __future__ import annotations

import csv
import html
import json
from datetime import date
from pathlib import Path

from .pricing import Analyse


def schrijf_csv(analyse: Analyse, pad: Path):
    pad.parent.mkdir(parents=True, exist_ok=True)
    with open(pad, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["voorstel_id", "sku", "naam", "rol", "richting", "huidige_prijs", "voorgestelde_prijs",
                    "wijziging_pct", "laagste_conc", "mediaan_conc", "positie_oud", "positie_nieuw",
                    "marge_oud", "marge_nieuw", "brutowinst_delta_week", "score", "reden"])
        for v in analyse.voorstellen:
            w.writerow([v.id or "", v.sku, v.naam, v.rol, v.richting,
                        f"{v.huidige_prijs:.2f}".replace(".", ","), f"{v.voorgestelde_prijs:.2f}".replace(".", ","),
                        f"{v.wijziging_pct:.1%}", f"{v.laagste:.2f}".replace(".", ","),
                        f"{v.mediaan:.2f}".replace(".", ","), v.positie_oud, v.positie_nieuw,
                        f"{v.marge_oud:.1%}", f"{v.marge_nieuw:.1%}",
                        f"{v.brutowinst_delta_week:.2f}".replace(".", ","), f"{v.score:.1f}".replace(".", ","),
                        v.reden])


def _eur(x: float) -> str:
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"


def _link(url: str) -> str:
    if not url or not url.startswith("http") or "demo.invalid" in url:
        return ""
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">bekijk</a>'


def _pct(x: float, teken=True) -> str:
    return (f"{x:+.1%}" if teken else f"{x:.1%}").replace(".", ",")


def schrijf_html(analyse: Analyse, pad: Path, datum: date, scrape_status: list[dict] | None = None):
    pad.parent.mkdir(parents=True, exist_ok=True)
    vs = analyse.voorstellen
    omhoog = [v for v in vs if v.richting == "omhoog"]
    omlaag = [v for v in vs if v.richting == "omlaag"]
    bw = sum(v.brutowinst_delta_week for v in vs)

    rijen = []
    for i, v in enumerate(vs):
        conc = "".join(
            f"<tr class='{'uit' if c.uitschieter else ''}'><td>{html.escape(c.naam)}</td>"
            f"<td class='num'>{_eur(c.prijs)}</td>"
            f"<td>{'actie' if c.actie else ''}{' · afwijkend' if c.uitschieter else ''}</td>"
            f"<td>{_link(c.url)}</td></tr>"
            for c in v.concurrenten)
        # mini-staafje: positie van oude/nieuwe prijs binnen de bandbreedte
        lo, hi = min(v.laagste, v.voorgestelde_prijs, v.huidige_prijs), max(v.hoogste, v.voorgestelde_prijs, v.huidige_prijs)
        sp = (hi - lo) or 1
        dots = "".join(f"<i class='c' style='left:{(c.prijs - lo) / sp * 100:.1f}%'></i>"
                       for c in v.concurrenten if not c.uitschieter)
        band = (f"<div class='band' title='concurrenten (grijs), nu (open), voorstel (gevuld)'>{dots}"
                f"<i class='o' style='left:{(v.huidige_prijs - lo) / sp * 100:.1f}%'></i>"
                f"<i class='n {v.richting}' style='left:{(v.voorgestelde_prijs - lo) / sp * 100:.1f}%'></i></div>")
        rijen.append(f"""
<tr class="rij" data-i="{i}">
  <td><input type="checkbox" class="ok" data-i="{i}" checked aria-label="akkoord {html.escape(v.sku)}"></td>
  <td><div class="naam">{html.escape(v.naam)}</div><div class="sub">{html.escape(v.sku)} · {html.escape(v.categorie)} · {html.escape(v.rol)}</div></td>
  <td class="num">{_eur(v.huidige_prijs)}</td>
  <td class="num"><b>{_eur(v.voorgestelde_prijs)}</b><div class="sub {v.richting}">{'▲' if v.richting == 'omhoog' else '▼'} {_pct(v.wijziging_pct)}</div></td>
  <td>{band}<div class="sub">{_eur(v.laagste)} – {_eur(v.hoogste)} · {v.aantal_concurrenten} conc.</div></td>
  <td class="num">{v.positie_oud} → {v.positie_nieuw}</td>
  <td class="num">{_pct(v.marge_oud, False)} → {_pct(v.marge_nieuw, False)}</td>
  <td class="num {'omhoog' if v.brutowinst_delta_week >= 0 else 'omlaag'}">{_eur(v.brutowinst_delta_week)}</td>
  <td class="reden">{html.escape(v.reden)}<details><summary>concurrenten</summary><table class="mini">{conc}</table></details></td>
</tr>""")

    sig_html = "".join(
        f"<li><span class='tag {s.soort}'>{html.escape(s.soort.replace('_', ' '))}</span> "
        f"<b>{html.escape(s.sku)}</b> – {html.escape(s.tekst)}</li>"
        for s in sorted(analyse.signalen, key=lambda s: ("onder_bodem", "uitschieter", "actie", "te_weinig_data").index(s.soort)
                        if s.soort in ("onder_bodem", "uitschieter", "actie", "te_weinig_data") else 9)[:60])

    status_html = ""
    if scrape_status:
        per = {}
        for r in scrape_status:
            per.setdefault(r["concurrent"], {})[r["status"]] = r["n"]
        status_html = "".join(
            f"<tr><td>{html.escape(c)}</td><td class='num'>{d.get('ok', 0)}</td>"
            f"<td class='num'>{sum(n for k, n in d.items() if k != 'ok')}</td>"
            f"<td class='num'>{d.get('ok', 0) / max(1, sum(d.values())):.0%}</td></tr>"
            for c, d in sorted(per.items()))

    export = json.dumps([{"id": v.id, "sku": v.sku, "naam": v.naam, "oud": v.huidige_prijs,
                          "nieuw": v.voorgestelde_prijs} for v in vs])

    doc = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prijsvoorstellen {datum.isoformat()}</title>
<style>
:root{{--bg:#f7f6f3;--card:#fff;--tx:#1d1d1b;--mu:#6b6a66;--ln:#e4e2dc;--up:#1f7a4d;--dn:#b4462b;--ac:#8a5a2b;--dot:#b9b6ae}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#161614;--card:#1f1f1c;--tx:#ecebe6;--mu:#a3a199;--ln:#34332f;--up:#5cc28f;--dn:#ef8a6c;--ac:#d6a26b;--dot:#6c6a64}}}}
:root[data-theme=dark]{{--bg:#161614;--card:#1f1f1c;--tx:#ecebe6;--mu:#a3a199;--ln:#34332f;--up:#5cc28f;--dn:#ef8a6c;--ac:#d6a26b;--dot:#6c6a64}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1280px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:32px 0 10px}}.sub{{color:var(--mu);font-size:12px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:12px 14px}}.kpi b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--ln);border-radius:10px}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:9px 10px;border-bottom:1px solid var(--ln);vertical-align:top;text-align:left}}
th{{font-size:12px;color:var(--mu);font-weight:600;white-space:nowrap;position:sticky;top:0;background:var(--card)}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}.naam{{font-weight:600;min-width:200px}}
.omhoog{{color:var(--up)}}.omlaag{{color:var(--dn)}}.reden{{min-width:260px;max-width:380px;font-size:13px}}
details summary{{cursor:pointer;color:var(--ac);font-size:12px;margin-top:4px}}table.mini td{{padding:3px 6px;font-size:12px;border:0}}tr.uit td{{opacity:.5;text-decoration:line-through}}
.band{{position:relative;height:14px;min-width:140px;margin:3px 6px;border-bottom:2px solid var(--ln)}}
.band i{{position:absolute;top:2px;width:9px;height:9px;margin-left:-4.5px;border-radius:50%}}
.band .c{{background:var(--dot)}}.band .o{{border:2px solid var(--tx);background:var(--card)}}.band .n.omhoog{{background:var(--up)}}.band .n.omlaag{{background:var(--dn)}}
tr.rij.uitgevinkt td:not(:first-child){{opacity:.4}}
.bar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}}
button{{font:inherit;background:var(--ac);color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer}}button.sec{{background:transparent;color:var(--tx);border:1px solid var(--ln)}}
ul.sig{{list-style:none;padding:0;margin:0;background:var(--card);border:1px solid var(--ln);border-radius:10px}}ul.sig li{{padding:8px 12px;border-bottom:1px solid var(--ln);font-size:13px}}
.tag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--ln);color:var(--mu);margin-right:4px}}.tag.onder_bodem{{color:var(--dn);border-color:var(--dn)}}.tag.uitschieter{{color:var(--ac);border-color:var(--ac)}}
</style></head><body><main>
<h1>Prijsvoorstellen Houthandel Gorinchem</h1>
<div class="sub">{datum.strftime('%d-%m-%Y')} · {analyse.geanalyseerd} artikelen geanalyseerd · {analyse.kandidaten} kandidaat-wijzigingen · top {len(vs)} geselecteerd</div>
<div class="kpis">
 <div class="kpi"><span class="sub">Voorstellen</span><b>{len(vs)}</b></div>
 <div class="kpi"><span class="sub">Verhogen</span><b class="omhoog">{len(omhoog)}</b></div>
 <div class="kpi"><span class="sub">Verlagen</span><b class="omlaag">{len(omlaag)}</b></div>
 <div class="kpi"><span class="sub">Verwacht effect brutowinst / week</span><b class="{'omhoog' if bw >= 0 else 'omlaag'}">{_eur(bw)}</b></div>
 <div class="kpi"><span class="sub">Signalen ter controle</span><b>{len(analyse.signalen)}</b></div>
</div>
<div class="bar"><button id="exp">Exporteer goedgekeurde prijzen (CSV)</button><button class="sec" id="alles">Alles aan/uit</button>
<span class="sub" id="telling"></span></div>
<div class="wrap"><table>
<thead><tr><th>Akkoord</th><th>Artikel</th><th class="num">Nu</th><th class="num">Voorstel</th><th>Markt</th><th class="num">Positie</th><th class="num">Marge</th><th class="num">Δ brutowinst/wk</th><th>Onderbouwing</th></tr></thead>
<tbody>{''.join(rijen) or '<tr><td colspan=9>Geen voorstellen vandaag.</td></tr>'}</tbody></table></div>
<p class="sub">Positie: 1 = goedkoopste van de markt. Marge = brutomarge op verkoopprijs excl. btw. Δ brutowinst is een schatting op basis van weekafzet en prijselasticiteit per artikelrol.</p>
<h2>Signalen ter controle</h2>
<ul class="sig">{sig_html or '<li>Geen bijzonderheden.</li>'}</ul>
{'<h2>Scrape-status (laatste run)</h2><div class="wrap"><table><thead><tr><th>Concurrent</th><th class="num">Gelukt</th><th class="num">Mislukt</th><th class="num">Slagingspercentage</th></tr></thead><tbody>' + status_html + '</tbody></table></div>' if status_html else ''}
</main>
<script>
const V={export};
const boxes=[...document.querySelectorAll('input.ok')];
function upd(){{let n=0;boxes.forEach(b=>{{b.closest('tr').classList.toggle('uitgevinkt',!b.checked);if(b.checked)n++}});document.getElementById('telling').textContent=n+' van '+boxes.length+' geselecteerd';}}
boxes.forEach(b=>b.addEventListener('change',upd));upd();
document.getElementById('alles').onclick=()=>{{const aan=!boxes.every(b=>b.checked);boxes.forEach(b=>b.checked=aan);upd();}};
document.getElementById('exp').onclick=()=>{{
 const r=[['voorstel_id','sku','naam','oude_prijs','nieuwe_prijs']];
 boxes.forEach(b=>{{if(b.checked){{const v=V[+b.dataset.i];r.push([v.id??'',v.sku,'"'+v.naam.replace(/"/g,'""')+'"',v.oud.toFixed(2).replace('.',','),v.nieuw.toFixed(2).replace('.',',')])}}}});
 const blob=new Blob(['\\ufeff'+r.map(x=>x.join(';')).join('\\n')],{{type:'text/csv'}});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='goedgekeurde-prijzen-{datum.isoformat()}.csv';a.click();
}};
</script></body></html>"""
    pad.write_text(doc, encoding="utf-8")
