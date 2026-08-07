"""Work Log standalone export hook.

Snapshots structured days, then mirrors the live page's read/write routes in
the browser.  The single HTML file keeps edits in the export shim's store and
uses the same portable JSON contract as the live importer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp


async def export_state(app: WorklogApp) -> dict:
    payload = await app.portable_payload()
    return {
        "days": payload["days"],
        "default_employer": app._default_employer(),
        "default_status": app._default_status(),
    }


def stub_routes() -> dict:
    return {}


def client_overrides() -> str:
    return r"""
// Work Log export — the same route-shaped UI, backed by one persisted day list.
(function(){
  if (!window.EOS_EXPORT) return;
  var KEY = '/worklog/offline/days';
  var FORMAT = 'emptyos.worklog';
  var VERSION = 1;
  var STATUSES = ['complete','in-progress','todo','next','waiting','review','blocked'];

  function clone(x){ return JSON.parse(JSON.stringify(x)); }
  function query(req){ return new URL(req.url, 'http://offline').searchParams; }
  // Local calendar date — `toISOString()` is UTC and names yesterday for the
  // first hours of every day east of Greenwich, which would file work under
  // the wrong day and disagree with the live app's `date.today()`.
  function isoDate(d){
    d = d || new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0')
         + '-' + String(d.getDate()).padStart(2,'0');
  }
  function today(){ return isoDate(); }
  function daysAgo(n){ return isoDate(new Date(Date.now() - n*86400000)); }
  // Calendar-component check: a Date round trip through UTC rejects every
  // valid date at UTC+13/+14, where local noon is already the previous day.
  function validDate(s){
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
    if(!m) return false;
    var y=+m[1], mo=+m[2], dd=+m[3], probe=new Date(Date.UTC(y, mo-1, dd));
    return probe.getUTCFullYear()===y && probe.getUTCMonth()===mo-1 && probe.getUTCDate()===dd;
  }
  function weekday(dateS){
    return new Date(dateS + 'T12:00:00').toLocaleDateString(undefined, {weekday:'long'});
  }
  function status(value){ return STATUSES.indexOf(value) >= 0 ? value : null; }
  function emptyDay(dateS, employer){
    return {date:dateS, employer:employer||'', plan:'', update:'', projects:[], timesheet:[], notes:[]};
  }
  function normalDay(raw){
    raw = raw || {};
    var day = emptyDay(String(raw.date||''), String(raw.employer||''));
    day.plan = String(raw.plan||''); day.update = String(raw.update||'');
    (raw.projects||[]).forEach(function(g){
      if(!g) return;
      var group = {project:String(g.project||'General'), items:[]};
      (g.items||[]).forEach(function(it){
        var text = String((it&&it.text)||'').trim();
        if(text) group.items.push({text:text, status:status(it.status)});
      });
      if(group.items.length) day.projects.push(group);
    });
    // Same guard as portable.py's normalize_day: a non-finite or out-of-range
    // hours value would round-trip to null and be dropped on the way back in.
    day.timesheet = (raw.timesheet||[]).filter(Boolean).map(function(t){
      return {project:String(t.project||'General'), hours:Number(t.hours||0), note:String(t.note||'')};
    }).filter(function(t){ return isFinite(t.hours) && t.hours >= 0 && t.hours <= 24; });
    day.notes = (raw.notes||[]).map(String).filter(Boolean);
    return day;
  }
  async function loadDays(){
    var stored = await window.EOS_EXPORT.get(KEY);
    var source = Array.isArray(stored) ? stored : ((window.EOS_EXPORT_DATA||{}).days||[]);
    return source.map(normalDay).sort(function(a,b){ return b.date.localeCompare(a.date); });
  }
  async function saveDays(days){
    days.sort(function(a,b){ return b.date.localeCompare(a.date); });
    await window.EOS_EXPORT.set(KEY, days);
    window.EOS_EXPORT_DATA.days = clone(days);
  }
  function filtered(days, employer){
    if(!employer) return days;
    return days.filter(function(d){ return d.employer.toLowerCase() === employer.toLowerCase(); });
  }
  function counts(day){
    var out = {};
    day.projects.forEach(function(g){ g.items.forEach(function(it){ if(it.status) out[it.status]=(out[it.status]||0)+1; }); });
    return out;
  }
  function summary(day){
    var groups = day.projects.filter(function(g){ return g.items.length; });
    return {date:day.date, weekday:weekday(day.date), employer:day.employer,
      projects:groups.map(function(g){return g.project;}), project_count:groups.length,
      item_count:groups.reduce(function(n,g){return n+g.items.length;},0),
      status_counts:counts(day), has_plan:!!day.plan};
  }
  function findDay(days, dateS){
    for(var i=0;i<days.length;i++) if(days[i].date===dateS) return days[i];
    return null;
  }
  function receipt(){
    return {ok:true,preview:false,days_seen:0,days_created:0,days_updated:0,days_unchanged:0,
      items_added:0,statuses_updated:0,timesheet_added:0,notes_added:0,prose_added:0,
      conflicts:[],changes:[]};
  }
  function extractImport(body){
    var doc = (body&&body.document)||body||{};
    if(doc.app_id==='worklog'){
      var kv=doc.kv||{}, ex=doc.export_data||{};
      doc={format:FORMAT,version:VERSION,days:Array.isArray(kv[KEY])?kv[KEY]:(ex.days||[])};
    }
    if(doc.format!==FORMAT) throw new Error("expected format '"+FORMAT+"'");
    if(doc.version!==VERSION) throw new Error('unsupported worklog format version: '+doc.version);
    if(!Array.isArray(doc.days)) throw new Error('worklog import is missing a days array');
    if(doc.days.length>10000) throw new Error('worklog import exceeds 10000 days');
    var seen={}, itemCount=0;
    return doc.days.map(function(raw){
      var day=normalDay(raw);
      if(!validDate(day.date)) throw new Error('bad worklog date: '+(day.date||'(empty)'));
      if(seen[day.date]) throw new Error('duplicate worklog day: '+day.date);
      seen[day.date]=1; day.projects.forEach(function(g){itemCount+=g.items.length;});
      if(itemCount>200000) throw new Error('worklog import exceeds 200000 items');
      return day;
    });
  }
  function mergeInto(dst, src, out){
    if(dst.employer && src.employer && dst.employer.toLowerCase()!==src.employer.toLowerCase()) return ['employer'];
    var changed=false, conflicts=[];
    if(!dst.employer&&src.employer){dst.employer=src.employer;changed=true;}
    ['plan','update'].forEach(function(field){
      if(!dst[field]&&src[field]){dst[field]=src[field];out.prose_added++;changed=true;}
      else if(dst[field]&&src[field]&&dst[field]!==src[field]) conflicts.push(field);
    });
    src.projects.forEach(function(sg){
      var dg=dst.projects.find(function(g){return g.project.toLowerCase()===sg.project.toLowerCase();});
      if(!dg){dg={project:sg.project,items:[]};dst.projects.push(dg);}
      sg.items.forEach(function(si){
        var di=dg.items.find(function(it){return it.text===si.text;});
        if(!di){dg.items.push(clone(si));out.items_added++;changed=true;}
        else if(di.status!==si.status){di.status=si.status;out.statuses_updated++;changed=true;}
      });
    });
    src.timesheet.forEach(function(st){
      var hit=dst.timesheet.some(function(dt){return dt.project.toLowerCase()===st.project.toLowerCase()&&dt.hours===st.hours&&dt.note===st.note;});
      if(!hit){dst.timesheet.push(clone(st));out.timesheet_added++;changed=true;}
    });
    src.notes.forEach(function(note){if(dst.notes.indexOf(note)<0){dst.notes.push(note);out.notes_added++;changed=true;}});
    return {changed:changed,conflicts:conflicts};
  }

  window.EOS_EXPORT.registerRoute('GET','/worklog/api/day',async function(req){
    var p=query(req), dateS=p.get('date')||today(), days=await loadDays(), day=findDay(days,dateS);
    if(!day) return Object.assign(emptyDay(dateS,(window.EOS_EXPORT_DATA||{}).default_employer||''),{weekday:weekday(dateS),exists:false});
    return Object.assign(clone(day),{weekday:weekday(dateS),exists:true,status_counts:counts(day)});
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/recent',async function(req){
    var p=query(req), n=Number(p.get('days')||30), employer=p.get('employer')||'', days=filtered(await loadDays(),employer);
    var cutoff=n>0?daysAgo(n):'';
    var rows=days.filter(function(d){return !cutoff||d.date>=cutoff;}).map(summary);
    return {days:rows,count:rows.length};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/projects',async function(req){
    var employer=query(req).get('employer')||'', map={};
    filtered(await loadDays(),employer).forEach(function(d){d.projects.forEach(function(g){map[g.project]=(map[g.project]||0)+g.items.length;});});
    var rows=Object.keys(map).map(function(name){return {name:name,items:map[name]};}).sort(function(a,b){return b.items-a.items;});
    return {projects:rows};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/employers',async function(){
    var map={};(await loadDays()).forEach(function(d){var e=d.employer||'—';map[e]=(map[e]||0)+1;});
    return {employers:Object.keys(map).map(function(name){return {name:name,days:map[name]};}).sort(function(a,b){return b.days-a.days;}),default:(window.EOS_EXPORT_DATA||{}).default_employer||''};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/by-project',async function(req){
    var p=query(req), name=(p.get('name')||'').toLowerCase(), employer=p.get('employer')||'';
    if(!name) return {error:'name required'};
    var rows=[];filtered(await loadDays(),employer).forEach(function(d){var items=[];d.projects.forEach(function(g){if(g.project.toLowerCase().indexOf(name)>=0)items=items.concat(clone(g.items));});if(items.length)rows.push({date:d.date,weekday:weekday(d.date),employer:d.employer,items:items});});
    return {project:name,days:rows,day_count:rows.length};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/heatmap',async function(req){
    var p=query(req), employer=p.get('employer')||'', year=p.get('year')||'', data={};
    filtered(await loadDays(),employer).forEach(function(d){if(!year||d.date.indexOf(year)===0)data[d.date]=d.projects.reduce(function(n,g){return n+g.items.length;},0);});return {data:data};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/month',async function(req){
    var p=query(req), month=p.get('month')||today().slice(0,7), employer=p.get('employer')||'', cells=[];
    filtered(await loadDays(),employer).forEach(function(d){if(d.date.indexOf(month)!==0)return;cells.push({date:d.date,items:d.projects.slice(0,4).map(function(g){return {id:g.project,label:g.project};})});});return {month:month,cells:cells};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/status-rollup',async function(req){
    var p=query(req), n=Number(p.get('days')||14), employer=p.get('employer')||'', cutoff=daysAgo(n), totals={},blocked=[],review=[];
    filtered(await loadDays(),employer).forEach(function(d){if(d.date<cutoff)return;d.projects.forEach(function(g){g.items.forEach(function(it){if(it.status)totals[it.status]=(totals[it.status]||0)+1;var row={date:d.date,project:g.project,text:it.text};if(it.status==='blocked')blocked.push(row);else if(it.status==='review')review.push(row);});});});return {totals:totals,blocked:blocked,review:review};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/carryover',async function(req){
    var employer=query(req).get('employer')||'', days=filtered(await loadDays(),employer), open=['in-progress','blocked','waiting','todo','next'];
    for(var i=0;i<days.length;i++){if(days[i].date>=today())continue;var items=[];days[i].projects.forEach(function(g){g.items.forEach(function(it){if(open.indexOf(it.status)>=0)items.push({project:g.project,text:it.text,status:it.status});});});return {from:days[i].date,weekday:weekday(days[i].date),items:items};}return {from:'',items:[]};
  });
  window.EOS_EXPORT.registerRoute('GET','/worklog/api/portable',async function(){return {format:FORMAT,version:VERSION,exported_at:new Date().toISOString(),days:await loadDays()};});

  window.EOS_EXPORT.registerRoute('POST','/worklog/api/log',async function(req){
    var b=req.body||{}, text=String(b.text||'').trim();if(!text)return {error:'text required'};
    var days=await loadDays(), dateS=String(b.date||today()), employer=String(b.employer||''), day=findDay(days,dateS);
    if(!day){day=emptyDay(dateS,employer||(window.EOS_EXPORT_DATA||{}).default_employer||'');days.push(day);}
    else if(employer&&day.employer&&employer.toLowerCase()!==day.employer.toLowerCase())return {error:dateS+' is already categorized under '+day.employer+'; cannot log it under '+employer};
    if(employer&&!day.employer)day.employer=employer;
    var project=String(b.project||'General').trim()||'General', group=day.projects.find(function(g){return g.project.toLowerCase()===project.toLowerCase();});
    if(!group){group={project:project,items:[]};day.projects.push(group);}group.items.push({text:text,status:status(b.status)||(window.EOS_EXPORT_DATA||{}).default_status||'in-progress'});
    await saveDays(days);return {ok:true,date:dateS,project:project,employer:day.employer,offline:true};
  });
  window.EOS_EXPORT.registerRoute('POST','/worklog/api/status',async function(req){
    var b=req.body||{}, days=await loadDays(), day=findDay(days,String(b.date||today())), hit=false;if(!day)return {error:'no worklog for that day'};
    day.projects.forEach(function(g){if(g.project.toLowerCase()!==String(b.project||'').toLowerCase())return;g.items.forEach(function(it){if(it.text===String(b.item||'')){it.status=status(b.status);hit=true;}});});
    if(!hit)return {error:'item not found'};await saveDays(days);return {ok:true,offline:true};
  });
  ['plan','update'].forEach(function(field){window.EOS_EXPORT.registerRoute('POST','/worklog/api/'+field,async function(req){var b=req.body||{},days=await loadDays(),dateS=String(b.date||today()),day=findDay(days,dateS);if(!day){day=emptyDay(dateS,(window.EOS_EXPORT_DATA||{}).default_employer||'');days.push(day);}day[field]=String(b.text||'').trim();await saveDays(days);return {ok:true,offline:true};});});
  // Mirrors app.py's import_portable: `dryRun` computes the identical receipt
  // without persisting, so the offline UI gets the same preview/confirm gate.
  var COUNTERS = ['items_added','statuses_updated','timesheet_added','notes_added','prose_added'];
  async function runImport(body, dryRun){
    var incoming = extractImport(body);
    var days = await loadDays();
    var out = receipt();
    out.preview = !!dryRun;
    out.days_seen = incoming.length;
    incoming.forEach(function(src){
      var before = {};
      COUNTERS.forEach(function(k){ before[k] = out[k]; });
      var dst = findDay(days, src.date), created = !dst, changed = true;
      if(created){
        days.push(clone(src));
        out.items_added += src.projects.reduce(function(n,g){ return n+g.items.length; }, 0);
        out.timesheet_added += src.timesheet.length;
        out.notes_added += src.notes.length;
        out.prose_added += (src.plan?1:0) + (src.update?1:0);
      } else {
        var merged = mergeInto(dst, src, out);
        if(Array.isArray(merged)){
          out.conflicts.push({date:src.date, fields:merged});
          changed = false;
        } else {
          if(merged.conflicts.length) out.conflicts.push({date:src.date, fields:merged.conflicts});
          changed = merged.changed;
        }
      }
      if(!changed){ out.days_unchanged++; return; }
      if(created) out.days_created++; else out.days_updated++;
      var row = {date:src.date, created:created};
      COUNTERS.forEach(function(k){ row[k] = out[k] - before[k]; });
      out.changes.push(row);
    });
    if(!dryRun) await saveDays(days);
    return out;
  }
  window.EOS_EXPORT.registerRoute('POST','/worklog/api/import/preview',function(req){ return runImport(req.body, true); });
  window.EOS_EXPORT.registerRoute('POST','/worklog/api/import',function(req){ return runImport(req.body, false); });
})();
"""
