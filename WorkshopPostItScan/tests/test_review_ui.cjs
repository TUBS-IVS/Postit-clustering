// Oberfläche ohne Browser: Formulare, Polling und Konflikte mit simuliertem DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() { this.value=''; this.checked=false; this.hidden=false; this.disabled=false;
    this.textContent=''; this.style={}; this.children=[]; this.handlers={};
    this.classList={toggle(){}}; }
  addEventListener(name, callback) { this.handlers[name]=callback; }
  replaceChildren(...children) { this.children=children; }
  scrollTo() {}
}
const elements=new Map();
const get=id=>{if(!elements.has(id)) elements.set(id,new Element());return elements.get(id);};
get('filter').value='all';
const base={bild:'photos/one.jpg',adresse:'MA11',text:'Original',text_englisch:'English',status:'prüfen',
  hinweis:'',fehler:'',geprueft:false,pruefung_veraltet:false,_basis_hash:'first',_revision:0,
  _scan:{adresse:'MA11',text:'Original',text_englisch:'English'}};
let snapshot={items:[base],warnung:''}, saved=null;
const context={console, setInterval(){},confirm:()=>true,
  document:{getElementById:get,createElement:()=>new Element(),addEventListener(){}},
  window:{addEventListener(){}},
  fetch:async(url, options)=>{
    if(options?.method==='POST') {
      saved=JSON.parse(options.body);
      if(saved.basis_hash!==snapshot.items[0]._basis_hash)
        return {ok:false,json:async()=>({fehler:'Scan geändert'})};
      const row=snapshot.items[0];
      snapshot={items:[{...row,adresse:saved.adresse,text:saved.text,text_englisch:saved.text_englisch,
        geprueft:saved.geprueft,pruefung_veraltet:false,_revision:row._revision+1}],warnung:''};
      return {ok:true,json:async()=>({revision:snapshot.items[0]._revision})};
    }
    return {ok:true,json:async()=>structuredClone(snapshot)};
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../review_ui/app.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);

(async()=>{
  // Warten auf den initialen asynchronen Abruf.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('original').value,'Original');
  get('original').value='Manuell';run('setDirty(true)');
  snapshot.items[0]={...base,_basis_hash:'second',_scan:{...base._scan,text:'Neuer Scan'}};
  await run('refresh()');
  assert.equal(get('original').value,'Manuell','Polling darf offene Eingaben nicht ersetzen');
  assert.equal(get('basisChanged').hidden,false);
  await run('save()');
  assert.equal(saved,null,'Unbestätigte Scan-Änderung darf nicht gespeichert werden');
  get('acceptBasis').handlers.click();
  assert.equal(get('basisChanged').hidden,true);
  get('verified').checked=true;
  await run('save()');
  assert.equal(saved.text,'Manuell');
  assert.equal(saved.basis_hash,'second');
  assert.equal(get('reviewStatus').textContent,'Geprüft');
  assert.equal(get('dirty').hidden,true);
  // Während des Speicherns darf die Auswahl nicht wechseln.
  run('saving=true');
  assert.equal(run("select('photos/other.jpg')"),false);
  run('saving=false');
  // Texte werden über textContent dargestellt, ohne HTML-Auswertung.
  snapshot.items[0]._scan.text='<script>private()</script>';
  await run('refresh()');
  assert.ok(get('scanOriginal').textContent.includes('<script>private()</script>'));
  console.log('UI-Prüfung bestanden: offene Eingaben, Scan-Konflikte, Speichern, Navigation und sichere Textausgabe.');
})().catch(error=>{console.error(error);process.exitCode=1;});
