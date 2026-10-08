'use strict';
const $ = id => document.getElementById(id);
let rows = [], selected = null, basis = null, revision = 0, dirty = false;
let saving = false, loading = false, zoom = 1, sourceWarning = '', generation = 0;
let loadingFinished = Promise.resolve();
function notify(text, error=false) {
  $('message').textContent = text; $('message').hidden = !text;
  $('message').classList.toggle('warning', error);
}
function setDirty(value) { dirty=value; $('dirty').hidden=!value; }
function current() { return rows.find(row => row.bild === selected); }
function visible() {
  const query=$('search').value.trim().toLocaleLowerCase(), filter=$('filter').value;
  return rows.filter(row => {
    if (filter==='unreviewed' && row.geprueft) return false;
    if (filter==='reviewed' && !row.geprueft) return false;
    if (filter==='issues' && !['prüfen','fehler'].includes(row.status)) return false;
    return [row.bild,row.adresse,row.text,row.text_englisch].some(value => String(value || '').toLocaleLowerCase().includes(query));
  });
}
function list() {
  const filtered=visible(), options=[];
  for (const row of filtered) {
    const option=document.createElement('option'); option.value=row.bild;
    option.textContent=`${row.geprueft?'✓ ':row.pruefung_veraltet?'! ':''}${row.adresse || 'Ohne Adresse'} · ${row.bild.split('/').pop()}`;
    option.selected=row.bild===selected; options.push(option);
  }
  $('items').replaceChildren(...options);
  $('count').textContent=`${rows.length} Zettel · ${rows.filter(row=>row.geprueft).length} geprüft`;
  const index=filtered.findIndex(row=>row.bild===selected);
  $('position').textContent=index<0?`${filtered.length} im Filter`:`${index+1} / ${filtered.length} im Filter`;
  $('previous').disabled=index<=0;
  $('next').disabled=index<0 || index>=filtered.length-1;
}
function metadata(row) {
  $('scanStatus').textContent=`Scan: ${row.status || 'unbekannt'}`;
  $('reviewStatus').textContent=row.geprueft?'Geprüft':'Ungeprüft';
  $('hint').textContent=[row.hinweis,row.fehler].filter(Boolean).join('\n');
  $('stale').hidden=!row.pruefung_veraltet;
  $('basisChanged').hidden=row._basis_hash===basis;
  $('scanOriginal').textContent=`Adresse: ${row._scan.adresse || row._scan.adresse_roh || '—'}\n\nOriginaltext:\n${row._scan.text || '—'}\n\nEnglischer Text:\n${row._scan.text_englisch || '—'}`;
}
function select(key, force=false) {
  if (saving) { list(); return false; }
  if (dirty && !force && key!==selected && !confirm('Ungespeicherte Änderungen verwerfen?')) { list(); return false; }
  const row=rows.find(item=>item.bild===key);
  selected=row?key:null; setDirty(false); basis=row?row._basis_hash:null; revision=row?row._revision:0;
  $('fields').disabled=!row || !!sourceWarning || saving;
  if (row) {
    $('address').value=row.adresse || ''; $('original').value=row.text || '';
    $('english').value=row.text_englisch || ''; $('verified').checked=row.geprueft;
    $('filename').textContent=row.bild.split('/').pop(); metadata(row);
    $('photoError').hidden=true; $('photo').hidden=false;
    $('photo').src='/api/image?bild='+encodeURIComponent(row.bild); resetZoom();
  } else {
    $('address').value=''; $('original').value=''; $('english').value=''; $('verified').checked=false;
    $('filename').textContent='Kein Zettel ausgewählt'; $('photo').hidden=true;
    $('scanStatus').textContent=''; $('reviewStatus').textContent=''; $('hint').textContent='';
    $('scanOriginal').textContent=''; $('stale').hidden=true; $('basisChanged').hidden=true;
  }
  list(); return true;
}
async function refresh() {
  if (loading || saving) return;
  loading=true; const started=generation;
  let finishLoading; loadingFinished=new Promise(resolve=>{finishLoading=resolve;});
  try {
    const response=await fetch('/api/items');
    if (!response.ok) throw new Error('Scan-Daten konnten nicht geladen werden.');
    const data=await response.json(); if (started!==generation) return;
    rows=data.items; sourceWarning=data.warnung || '';
    $('warning').textContent=sourceWarning; $('warning').hidden=!sourceWarning;
    const row=current();
    if (dirty) {
      if (row) metadata(row);
      else notify('Der gewählte Zettel ist nicht mehr in den Scan-Daten. Eingaben bleiben erhalten.',true);
      $('fields').disabled=!row || !!sourceWarning;
      list();
    } else if (row) {
      // Eingaben nur bei tatsächlichen Änderungen neu setzen; Fokus/Zoom bleiben sonst erhalten.
      if (basis!==row._basis_hash || revision!==row._revision) select(row.bild,true);
      else { metadata(row); $('fields').disabled=!!sourceWarning; list(); }
    } else select(visible()[0]?.bild || null,true);
  } catch (error) { notify(error.message,true); }
  finally { loading=false; finishLoading(); }
}
function navigate(delta) {
  const filtered=visible(), index=filtered.findIndex(row=>row.bild===selected);
  const next=filtered[index+delta]; if (next) select(next.bild);
}
async function save(next=false) {
  if (!current() || saving || sourceWarning) return;
  if (current()._basis_hash!==basis) { notify('Bitte zuerst die neuen Scan-Daten unten vergleichen und bestätigen.',true); return; }
  const oldList=visible().map(row=>row.bild), oldIndex=oldList.indexOf(selected), oldKey=selected;
  const payload={bild:selected,adresse:$('address').value,text:$('original').value,
    text_englisch:$('english').value,geprueft:$('verified').checked,basis_hash:basis,revision};
  saving=true; generation++; $('fields').disabled=true;
  try {
    const response=await fetch('/api/corrections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json(); if (!response.ok) throw new Error(data.fehler || 'Speichern fehlgeschlagen.');
    revision=data.revision; setDirty(false); notify('Gespeichert.');
    await loadingFinished; saving=false; await refresh();
    if (!next && current()) select(selected,true);
    if (next) {
      const after=visible(), candidate=oldList.slice(oldIndex+1).find(key=>after.some(row=>row.bild===key));
      const fallback=after.find(row=>row.bild!==oldKey && !row.geprueft);
      select(candidate || fallback?.bild || oldKey,true);
    }
  } catch (error) { notify(error.message,true); }
  finally { saving=false; $('fields').disabled=!current() || !!sourceWarning; }
}
function resetZoom() { zoom=1; $('photo').style.width='100%'; $('photoFrame').scrollTo(0,0); }
function changeZoom(multiplier) { zoom=Math.min(5,Math.max(1,zoom*multiplier)); $('photo').style.width=`${zoom*100}%`; }
$('editorForm').addEventListener('submit',event=>{event.preventDefault();save();});
$('saveNext').addEventListener('click',()=>save(true));
for (const id of ['address','original','english','verified']) $(id).addEventListener('input',()=>setDirty(true));
$('items').addEventListener('change',()=>select($('items').value));
for (const id of ['search','filter']) $(id).addEventListener('input',()=>{list();if (!dirty && !visible().some(row=>row.bild===selected)) select(visible()[0]?.bild || null,true);});
$('previous').addEventListener('click',()=>navigate(-1)); $('next').addEventListener('click',()=>navigate(1));
$('reload').addEventListener('click',async()=>{if (saving || loading) return;if (dirty && !confirm('Ungespeicherte Änderungen verwerfen und Daten neu laden?')) return;setDirty(false);basis=null;await refresh();});
$('acceptBasis').addEventListener('click',()=>{const row=current();if(row){basis=row._basis_hash;setDirty(true);metadata(row);$('verified').checked=false;}});
$('zoomIn').addEventListener('click',()=>changeZoom(1.35));$('zoomOut').addEventListener('click',()=>changeZoom(1/1.35));$('zoomReset').addEventListener('click',resetZoom);
$('photo').addEventListener('error',()=>{$('photoError').hidden=false;$('photo').hidden=true;});
window.addEventListener('beforeunload',event=>{if(dirty || saving){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}});
refresh();setInterval(refresh,5000);
