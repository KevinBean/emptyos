import { defineView } from '/static/eos-cad-view.js';
import { PHASE_COLORS, substationObjects, bounds2d, projector, node, addGrid, selected } from '/static/eos-cad-views/substation-common.js';

const STYLES=[
'.cadv-sse{position:absolute;inset:0;display:flex;flex-direction:column;background:var(--bg);}',
'.cadv-sse .hd{display:flex;gap:8px;align-items:center;padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;}',
'.cadv-sse .ttl{text-transform:uppercase;letter-spacing:.08em;color:var(--muted);}',
'.cadv-sse button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:2px 6px;cursor:pointer;}',
'.cadv-sse .screen{margin-left:auto;color:var(--warning);}',
'.cadv-sse .body{position:relative;flex:1;min-height:0;}',
'.cadv-sse svg{width:100%;height:100%;display:block;}',
'.cadv-sse .grid{stroke:var(--border);stroke-width:.7;stroke-dasharray:2 3;}',
'.cadv-sse .ground{stroke:var(--success);stroke-width:1.5;}',
'.cadv-sse .equip{fill:color-mix(in srgb,var(--info) 10%,transparent);stroke:var(--border-strong);stroke-width:1.2;cursor:pointer;}',
'.cadv-sse .equip.sel{stroke:var(--accent);stroke-width:2.5;}',
'.cadv-sse .route{fill:none;stroke-width:2;}',
'.cadv-sse .env{fill:none;stroke:var(--warning);stroke-width:1;stroke-dasharray:5 4;opacity:.75;}',
'.cadv-sse .label{fill:var(--text);font-size:9px;}',
'.cadv-sse .fail{fill:var(--danger);stroke:var(--ink-on-vivid);stroke-width:1.2;}',
].join('');
const MARKUP='<div class="cadv-sse"><div class="hd"><span class="ttl">Elevation</span><button data-sse-axis>Front X-Z</button><span data-sse-meta></span><span class="screen">Screening only</span></div><div class="body" data-sse-body></div></div>';

function projectedWidth(p,axis){const s=p.size||[1,1,1],yaw=((p.rpy||[0,0,0])[2]||0)*Math.PI/180;return axis===0?Math.abs(Math.cos(yaw))*s[0]+Math.abs(Math.sin(yaw))*s[1]:Math.abs(Math.sin(yaw))*s[0]+Math.abs(Math.cos(yaw))*s[1];}
function routePoints(route,axis,proj){return route.map((p)=>proj(p[axis],p[2]).join(',')).join(' ');}
function render(vctx){
 const host=vctx.pane.querySelector('[data-sse-body]');if(!host)return;
 const axis=vctx._sseAxis||0,data=substationObjects(vctx.store),W=host.clientWidth||600,H=host.clientHeight||350;
 const b=bounds2d(data,[axis,2]);b.minB=Math.min(0,b.minB);const proj=projector(b,W,H,24,true);
 const root=node('svg',{viewBox:'0 0 '+W+' '+H,preserveAspectRatio:'xMidYMid meet'});addGrid(root,b,proj,5);
 const ga=proj(b.minA,0),gb=proj(b.maxA,0);root.appendChild(node('line',{x1:ga[0],y1:ga[1],x2:gb[0],y2:gb[1],class:'ground'}));
 data.equipment.forEach((o)=>{
   const p=o.props||{},xyz=p.xyz||[0,0,0],size=p.size||[1,1,1],w=projectedWidth(p,axis),clear=Number(p.clearance_m||0);
   const a=proj(xyz[axis]-w/2,xyz[2]+size[2]),z=proj(xyz[axis]+w/2,xyz[2]);
   if(clear>0){const ea=proj(xyz[axis]-w/2-clear,xyz[2]+size[2]+clear),ez=proj(xyz[axis]+w/2+clear,Math.max(0,xyz[2]-clear));root.appendChild(node('rect',{x:ea[0],y:ea[1],width:ez[0]-ea[0],height:ez[1]-ea[1],class:'env'}));}
   const rect=node('rect',{x:a[0],y:a[1],width:z[0]-a[0],height:z[1]-a[1],class:'equip'+(selected(vctx.store,o.oid)?' sel':'')});
   rect.addEventListener('click',()=>vctx.store.select(o.oid));root.appendChild(rect);
   root.appendChild(node('text',{x:a[0]+3,y:a[1]-4,class:'label'},p.label||o.oid));
 });
 data.connections.forEach((o)=>Object.entries((o.props||{}).phase_routes||{}).forEach(([phase,route])=>{
   const pl=node('polyline',{points:routePoints(route,axis,proj),class:'route',stroke:PHASE_COLORS[phase]||'#d29922'});
   pl.addEventListener('click',()=>vctx.store.select(o.oid));root.appendChild(pl);
 }));
 const checks=(((vctx.store.doc.ext||{})['engineering-scene']||{}).check_results)||[];
 checks.filter((r)=>r.passes===false&&r.connection).forEach((r)=>{
   const c=data.connections.find((o)=>o.oid===r.connection),routes=c&&c.props&&c.props.phase_routes,route=routes&&(routes.B||routes.A||routes.C);if(!route)return;
   const p=route[Math.floor(route.length/2)],q=proj(p[axis],p[2]);root.appendChild(node('circle',{cx:q[0],cy:q[1],r:6,class:'fail'}));
   root.appendChild(node('text',{x:q[0]+8,y:q[1]-7,class:'label'},r.check+' '+Math.round(r.actual_mm||0)+'/'+Math.round(r.required_mm||0)+' mm'));
 });
 host.innerHTML='';host.appendChild(root);
 const meta=vctx.pane.querySelector('[data-sse-meta]');if(meta)meta.textContent='5 m grid / ground datum 0 m';
}
const _v=defineView({id:'substation-elevation',styles:STYLES,markup:MARKUP,events:['doc','object','select'],
 mount(vctx){vctx._sseAxis=0;const b=vctx.pane.querySelector('[data-sse-axis]');b.addEventListener('click',()=>{vctx._sseAxis=vctx._sseAxis?0:1;b.textContent=vctx._sseAxis?'Right Y-Z':'Front X-Z';render(vctx);});render(vctx);vctx._sseResize=()=>render(vctx);window.addEventListener('resize',vctx._sseResize);},
 update(vctx){render(vctx);},
 teardown(vctx){if(vctx._sseResize)window.removeEventListener('resize',vctx._sseResize);}
});
export const mount=_v.mount;export const teardown=_v.teardown;

