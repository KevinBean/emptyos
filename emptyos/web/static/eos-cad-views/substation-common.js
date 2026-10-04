// Shared projection helpers for engineering-scene substation views.
//
// node()/projector() wrap the same SVG-element-builder + world->pixel-projector
// primitives eos-cad-corridor.js already exports (svg()/makeProjector()) rather
// than reimplementing them — see .claude/rules/multi-module-apps.md's Rule 9
// (extract on the second occurrence, don't silently copy).

import { svg, makeProjector } from '/static/eos-cad-corridor.js';

export const PHASE_COLORS = { A:'#d64545', B:'#d6ad2f', C:'#2f6fd6' };

export function substationObjects(store) {
  const rows = store.iterObjects ? store.iterObjects() : [];
  return {
    equipment: rows.filter((o) => o.kind === 'substation-equipment'),
    connections: rows.filter((o) => o.kind === 'substation-connection'),
  };
}

export function bounds2d(data, axes) {
  let minA=Infinity,maxA=-Infinity,minB=Infinity,maxB=-Infinity;
  const take=(a,b)=>{ minA=Math.min(minA,a); maxA=Math.max(maxA,a); minB=Math.min(minB,b); maxB=Math.max(maxB,b); };
  data.equipment.forEach((o)=>{
    const p=o.props||{}, xyz=p.xyz||[0,0,0], size=p.size||[1,1,1];
    const ai=axes[0], bi=axes[1];
    take(Number(xyz[ai])-Number(size[ai]||1)/2, Number(xyz[bi]));
    take(Number(xyz[ai])+Number(size[ai]||1)/2, Number(xyz[bi])+Number(size[bi]||1));
  });
  data.connections.forEach((o)=>Object.values((o.props||{}).phase_routes||{}).forEach((route)=>
    route.forEach((p)=>take(Number(p[axes[0]]),Number(p[axes[1]])))));
  if(!isFinite(minA)){minA=-5;maxA=45;minB=-10;maxB=10;}
  const ma=Math.max(5,(maxA-minA)*0.08), mb=Math.max(3,(maxB-minB)*0.10);
  return {minA:minA-ma,maxA:maxA+ma,minB:minB-mb,maxB:maxB+mb};
}

export function projector(bounds,w,h,pad,flipB) {
  const bbox={minx:bounds.minA,maxx:bounds.maxA,miny:bounds.minB,maxy:bounds.maxB};
  return makeProjector(bbox,w,h,pad,{flipY:flipB,noCenter:true});
}

export function node(name, attrs, text) {
  const el=svg(name,attrs);
  if(text!=null) el.textContent=String(text);
  return el;
}

export function addGrid(root,bounds,proj,step) {
  const startA=Math.floor(bounds.minA/step)*step, endA=Math.ceil(bounds.maxA/step)*step;
  const startB=Math.floor(bounds.minB/step)*step, endB=Math.ceil(bounds.maxB/step)*step;
  for(let a=startA;a<=endA;a+=step){ const p=proj(a,startB),q=proj(a,endB); root.appendChild(node('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],class:'grid'})); }
  for(let b=startB;b<=endB;b+=step){ const p=proj(startA,b),q=proj(endA,b); root.appendChild(node('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],class:'grid'})); }
}

export function selected(store,oid){ return String(store.selection||'')===String(oid||''); }

