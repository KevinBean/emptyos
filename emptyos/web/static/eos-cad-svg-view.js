// Shared equal-aspect SVG viewport used by engineering drawing views. It owns
// interaction and export only; callers continue to own and redraw the SVG.
export function createSvgViewport(host) {
  let svg = null, base = null, box = null, drag = null;
  function parse(value) { const n = String(value || '0 0 100 100').trim().split(/\s+/).map(Number); return { x:n[0], y:n[1], w:n[2], h:n[3] }; }
  function apply() { if (svg && box) svg.setAttribute('viewBox', `${box.x} ${box.y} ${box.w} ${box.h}`); }
  function fit() { if (!base) return; box = { ...base }; apply(); }
  function mount(next, viewBox) { const prior=base; const nextBase=parse(viewBox || next.getAttribute('viewBox')); const same=prior && prior.x===nextBase.x && prior.y===nextBase.y && prior.w===nextBase.w && prior.h===nextBase.h; svg=next; base=nextBase; if(!same || !box) box={...base}; host.innerHTML=''; host.appendChild(next); apply(); }
  function wheel(event) {
    if (!svg || !box) return; event.preventDefault();
    const rect=svg.getBoundingClientRect(), k=Math.exp(event.deltaY*0.0012);
    const px=box.x+(event.clientX-rect.left)/rect.width*box.w, py=box.y+(event.clientY-rect.top)/rect.height*box.h;
    box={x:px-(px-box.x)*k,y:py-(py-box.y)*k,w:box.w*k,h:box.h*k}; apply();
  }
  function down(event) { if (!svg || event.button !== 0) return; drag={x:event.clientX,y:event.clientY,box:{...box}}; host.setPointerCapture?.(event.pointerId); }
  function move(event) { if (!drag || !svg) return; const rect=svg.getBoundingClientRect(); box.x=drag.box.x-(event.clientX-drag.x)/rect.width*drag.box.w; box.y=drag.box.y-(event.clientY-drag.y)/rect.height*drag.box.h; apply(); }
  function up() { drag=null; }
  function serialize() {
    if (!svg) return '';
    const copy=svg.cloneNode(true);
    copy.setAttribute('xmlns','http://www.w3.org/2000/svg');
    copy.setAttribute('viewBox',`${base.x} ${base.y} ${base.w} ${base.h}`);
    // Blob-backed SVGs do not inherit the host page's theme variables. Embed
    // resolved values so standalone SVG and PNG exports match the live view.
    const computed=getComputedStyle(document.documentElement);
    const names=['bg','bg-card','text','muted','border','accent','danger','warning','success','mono'];
    const values=names.map(name=>`--${name}:${computed.getPropertyValue(`--${name}`).trim() || 'initial'}`).join(';');
    const style=document.createElementNS('http://www.w3.org/2000/svg','style');
    style.textContent=`:root{${values}}`;
    copy.insertBefore(style,copy.firstChild);
    return new XMLSerializer().serializeToString(copy);
  }
  function download(blob,filename) { const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=filename; a.style.display='none'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); }
  function exportSvg(filename='drawing.svg') { download(new Blob([serialize()],{type:'image/svg+xml'}),filename); }
  async function exportPng(filename='drawing.png',scale=2) {
    if (!svg || !base) return; const image=new Image(), url=URL.createObjectURL(new Blob([serialize()],{type:'image/svg+xml'}));
    await new Promise((resolve,reject)=>{ image.onload=resolve; image.onerror=reject; image.src=url; });
    const canvas=document.createElement('canvas'); canvas.width=Math.max(1,Math.round(base.w*scale)); canvas.height=Math.max(1,Math.round(base.h*scale));
    const ctx=canvas.getContext('2d'); ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--bg') || '#fff'; ctx.fillRect(0,0,canvas.width,canvas.height); ctx.drawImage(image,0,0,canvas.width,canvas.height); URL.revokeObjectURL(url);
    const blob=await new Promise((resolve)=>canvas.toBlob(resolve,'image/png')); if (blob) download(blob,filename);
  }
  host.addEventListener('wheel',wheel,{passive:false}); host.addEventListener('pointerdown',down); host.addEventListener('pointermove',move); host.addEventListener('pointerup',up); host.addEventListener('pointercancel',up);
  return { mount,fit,serialize,exportSvg,exportPng,destroy(){ host.removeEventListener('wheel',wheel); host.removeEventListener('pointerdown',down); host.removeEventListener('pointermove',move); host.removeEventListener('pointerup',up); host.removeEventListener('pointercancel',up); } };
}
