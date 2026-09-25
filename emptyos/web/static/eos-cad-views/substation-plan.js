import { defineView } from '/static/eos-cad-view.js';
import { PHASE_COLORS, substationObjects, bounds2d, projector, node, addGrid, selected } from '/static/eos-cad-views/substation-common.js';

const STYLES = [
'.cadv-ss2d{position:absolute;inset:0;display:flex;flex-direction:column;background:var(--bg);}',
'.cadv-ss2d .hd{display:flex;gap:10px;align-items:center;padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;}',
'.cadv-ss2d .ttl{text-transform:uppercase;letter-spacing:.08em;color:var(--muted);}',
'.cadv-ss2d .basis{color:var(--text);font-family:var(--mono,monospace);}',
'.cadv-ss2d .screen{margin-left:auto;color:var(--warning);}',
'.cadv-ss2d .body{position:relative;flex:1;min-height:0;}',
'.cadv-ss2d svg{width:100%;height:100%;display:block;}',
'.cadv-ss2d .grid{stroke:var(--border);stroke-width:.7;stroke-dasharray:2 3;}',
'.cadv-ss2d .equip{fill:color-mix(in srgb,var(--info) 10%,transparent);stroke:var(--border-strong);stroke-width:1.2;cursor:pointer;}',
'.cadv-ss2d .equip.sel{stroke:var(--accent);stroke-width:2.5;}',
'.cadv-ss2d .env{fill:none;stroke:var(--warning);stroke-width:1;stroke-dasharray:5 4;opacity:.7;}',
'.cadv-ss2d .route{fill:none;stroke-width:2;}',
'.cadv-ss2d .term{stroke:var(--bg);stroke-width:.7;}',
'.cadv-ss2d .label{fill:var(--text);font-size:9px;pointer-events:none;}',
'.cadv-ss2d .dim{fill:var(--muted);font-size:9px;}',
'.cadv-ss2d .fail{fill:var(--danger);stroke:var(--ink-on-vivid);stroke-width:1.2;}',
].join('');

const MARKUP='<div class="cadv-ss2d"><div class="hd"><span class="ttl">Plan</span><span class="basis" data-ss-basis></span><span class="screen">Preliminary AS 2067 screening</span></div><div class="body" data-ss-body></div></div>';

function corners(props,extra){
  const p=props.xyz||[0,0,0],s=props.size||[1,1,1],r=(props.rpy||[0,0,0])[2]||0;
  const a=r*Math.PI/180,c=Math.cos(a),sn=Math.sin(a),hx=s[0]/2+extra,hy=s[1]/2+extra;
  return [[-hx,-hy],[hx,-hy],[hx,hy],[-hx,hy]].map(([x,y])=>[p[0]+x*c-y*sn,p[1]+x*sn+y*c]);
}
function pathPoints(points,proj){return points.map((p)=>proj(p[0],p[1]).join(',')).join(' ');}
function midpoint(route){const p=route[Math.floor(route.length/2)]||[0,0,0];return p;}

function render(vctx){
  const host=vctx.pane.querySelector('[data-ss-body]'); if(!host)return;
  const data=substationObjects(vctx.store),W=host.clientWidth||600,H=host.clientHeight||350;
  const b=bounds2d(data,[0,1]),proj=projector(b,W,H,24,true);
  const root=node('svg',{viewBox:'0 0 '+W+' '+H,preserveAspectRatio:'xMidYMid meet'});
  addGrid(root,b,proj,5);
  data.equipment.forEach((o)=>{
    const p=o.props||{},clear=Number(p.clearance_m||0);
    if(clear>0) root.appendChild(node('polygon',{points:pathPoints(corners(p,clear),proj),class:'env'}));
    const poly=node('polygon',{points:pathPoints(corners(p,0),proj),class:'equip'+(selected(vctx.store,o.oid)?' sel':'')});
    poly.addEventListener('click',()=>vctx.store.select(o.oid)); root.appendChild(poly);
    Object.values(p.terminal_frames||{}).forEach((t)=>{if(!t.phase)return;const q=proj(t.xyz[0],t.xyz[1]);root.appendChild(node('circle',{cx:q[0],cy:q[1],r:2.5,fill:PHASE_COLORS[t.phase]||'#fff',class:'term'}));});
    const q=proj((p.xyz||[0,0])[0],(p.xyz||[0,0])[1]);
    root.appendChild(node('text',{x:q[0]+4,y:q[1]-5,class:'label'},p.label||o.oid));
  });
  data.connections.forEach((o)=>{
    Object.entries((o.props||{}).phase_routes||{}).forEach(([phase,route])=>{
      const pl=node('polyline',{points:pathPoints(route,proj),class:'route',stroke:PHASE_COLORS[phase]||'#d29922'});
      pl.addEventListener('click',()=>vctx.store.select(o.oid));root.appendChild(pl);
    });
  });
  const checks=(((vctx.store.doc.ext||{})['engineering-scene']||{}).check_results)||[];
  checks.filter((r)=>r.passes===false&&r.connection).forEach((r)=>{
    const c=data.connections.find((o)=>o.oid===r.connection),routes=c&&c.props&&c.props.phase_routes;
    const route=routes&&(routes.B||routes.A||routes.C);if(!route)return;
    const p=midpoint(route),q=proj(p[0],p[1]);
    root.appendChild(node('circle',{cx:q[0],cy:q[1],r:6,class:'fail'}));
    root.appendChild(node('text',{x:q[0]+8,y:q[1]-7,class:'label'},r.check+' '+Math.round(r.actual_mm||0)+'/'+Math.round(r.required_mm||0)+' mm'));
  });
  const width=Math.max(0,b.maxA-b.minA-10),dim=proj(b.minA+5,b.minB+2);
  root.appendChild(node('text',{x:dim[0],y:dim[1],class:'dim'},'5 m grid / displayed extent '+width.toFixed(1)+' m'));
  host.innerHTML='';host.appendChild(root);
  const basis=((vctx.store.doc.ext||{})['engineering-scene']||{}).design_basis||{};
  const meta=vctx.pane.querySelector('[data-ss-basis]');if(meta)meta.textContent=(basis.voltage_kv||'?')+' kV / altitude '+(basis.altitude_m==null?'?':basis.altitude_m)+' m';
}

const _v=defineView({id:'substation-plan',styles:STYLES,markup:MARKUP,events:['doc','object','select'],
  mount(vctx){render(vctx);vctx._ssResize=()=>render(vctx);window.addEventListener('resize',vctx._ssResize);},
  update(vctx){render(vctx);},
  teardown(vctx){if(vctx._ssResize)window.removeEventListener('resize',vctx._ssResize);}
});
export const mount=_v.mount;export const teardown=_v.teardown;

