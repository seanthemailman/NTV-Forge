/* Shared retro picker: server directories only from localhost; file imports remain browser sandboxed. */
(function(){
const css=document.createElement('style');css.textContent=`
#forgePicker{position:fixed;inset:0;z-index:99999;background:#000c;display:none;align-items:center;justify-content:center;font:13px Consolas,monospace;color:#a6efc2}
#forgePicker.open{display:flex}#forgePicker .box{width:min(760px,94vw);max-height:85vh;display:flex;flex-direction:column;background:#0d1b20;border:2px solid #5be2bc;box-shadow:0 0 35px #15d5a440;padding:18px}
#forgePicker h2{margin:0 0 12px;color:#6af7d3;font-size:17px;letter-spacing:2px}#forgePicker .row{display:flex;gap:8px;margin-bottom:10px}
#forgePicker input{flex:1;min-width:0;background:#071215;border:1px solid #528e81;color:#c5ffe7;padding:10px;font:inherit}
#forgePicker button{background:#163c42;color:#b9ffdf;border:1px solid #56ad9a;padding:8px 12px;cursor:pointer;font:inherit;margin:0}
#forgePicker button:hover{background:#27615b}#forgePicker .items{overflow:auto;min-height:110px;max-height:48vh;border:1px solid #3c7772;padding:8px}
#forgePicker .items button{display:block;text-align:left;width:100%;margin:2px 0;border:0;background:transparent}
#forgePicker .items button:hover{background:#214844}#forgePicker .hint{font-size:11px;color:#91b5ad}`;document.head.appendChild(css);
const modal=document.createElement('div');modal.id='forgePicker';modal.innerHTML=`<div class="box" role="dialog" aria-modal="true" aria-label="Retro folder browser"><h2>▣ FORGE FILE NAVIGATOR</h2><div class="row"><button id="fpUp">↑ UP</button><input id="fpPath" aria-label="Folder path"><button id="fpGo">GO</button></div><div id="fpItems" class="items"></div><p class="hint">Browse folders on the Forge host. Mapped drives and UNC paths may also be typed.</p><div class="row"><button id="fpSelect">SELECT THIS FOLDER</button><button id="fpCancel">CANCEL</button></div></div>`;document.body.appendChild(modal);
let chosen='',parent='',callback=null;const path=modal.querySelector('#fpPath'),items=modal.querySelector('#fpItems');
async function navigate(p){items.textContent='Loading folders…';try{let r=await fetch('/api/folders?path='+encodeURIComponent(p),{cache:'no-store'}),d=await r.json();if(!r.ok)throw Error(d.error||'Folder unavailable');chosen=d.path;parent=d.parent;path.value=chosen;items.replaceChildren();if(!d.folders.length)items.textContent='No subfolders. You can select this location.';for(const f of d.folders){let b=document.createElement('button');b.textContent='▸ '+f.name;b.onclick=()=>navigate(f.path);items.appendChild(b)}}catch(e){items.textContent='ERROR: '+e.message}}
function close(){modal.classList.remove('open');callback=null}
modal.querySelector('#fpUp').onclick=()=>navigate(parent||'');modal.querySelector('#fpGo').onclick=()=>navigate(path.value);path.onkeydown=e=>{if(e.key==='Enter')navigate(path.value)};
modal.querySelector('#fpCancel').onclick=close;modal.querySelector('#fpSelect').onclick=()=>{if(!chosen)return;const fn=callback;close();if(fn)fn(chosen)};
modal.addEventListener('click',e=>{if(e.target===modal)close()});
window.forgePickFolder=(initial,cb)=>{callback=cb;modal.classList.add('open');navigate(initial||'')};
window.forgePickFile=async(input)=>{
 if(!input)return;
 // Native picker fallback for remote clients (server filesystem browsing is host-only).
 try{const check=await fetch('/api/files',{cache:'no-store'});if(!check.ok)throw Error('Host-only');
 const modal2=document.createElement('div');modal2.className='open';modal2.id='forgePicker';
 modal2.style.zIndex='100001';
 modal2.innerHTML='<div class="box" role="dialog" aria-modal="true"><h2>▣ FORGE FILE NAVIGATOR / SELECT FILE</h2><div class="row"><button id="fileUp">↑ UP</button><input id="filePath" aria-label="Folder path"><button id="fileGo">GO</button></div><div id="fileItems" class="items"></div><p class="hint">CSV / JSON files on this PC. Remote browsers use the native file picker.</p><div class="row"><button id="fileCancel">CANCEL</button><button id="fileNative">BROWSER PICKER</button></div></div>';
 document.body.appendChild(modal2);
 const field=modal2.querySelector('#filePath'),list=modal2.querySelector('#fileItems');let parent='';
 const close=()=>modal2.remove();
 const go=async(path)=>{list.textContent='Loading…';try{const r=await fetch('/api/files?path='+encodeURIComponent(path));const d=await r.json();if(!r.ok)throw Error(d.error);field.value=d.path;parent=d.parent;list.replaceChildren();for(const item of d.items){const b=document.createElement('button');b.textContent=(item.directory?'▸ ':'▣ ')+item.name;b.onclick=()=>item.directory?go(item.path):select(item.path);list.appendChild(b)}}catch(e){list.textContent='ERROR: '+e.message}};
 const select=async(path)=>{try{const r=await fetch('/api/file-read?path='+encodeURIComponent(path));if(!r.ok)throw Error(await r.text());const bytes=await r.blob();const file=new File([bytes],path.split(/[\\/]/).pop(),{type:input.accept.includes('csv')?'text/csv':'application/json'});try{const dt=new DataTransfer();dt.items.add(file);input.files=dt.files;close();input.dispatchEvent(new Event('change',{bubbles:true}));}catch(assignError){close();if(typeof window.forgeImportedFile==='function')window.forgeImportedFile(file);else input.click();}}catch(e){list.textContent='ERROR: '+e.message}};
 modal2.querySelector('#fileUp').onclick=()=>go(parent);modal2.querySelector('#fileGo').onclick=()=>go(field.value);field.onkeydown=e=>{if(e.key==='Enter')go(field.value)};
 modal2.querySelector('#fileCancel').onclick=close;modal2.querySelector('#fileNative').onclick=()=>{close();input.click()};modal2.onclick=e=>{if(e.target===modal2)close()};go('');
 }catch(e){input.click()}
};
})();