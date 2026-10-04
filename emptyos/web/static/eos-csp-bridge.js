/**
 * eos-csp-bridge.js — inline-handler bridge for CSP-locked pages (MV3
 * Chrome extensions: script-src 'self' — no unsafe-inline, no hashes).
 *
 * EmptyOS pages (and their innerHTML renderers) use inline `onclick="..."`
 * attributes. Under an extension's CSP those never execute. This bridge
 * re-enables them WITHOUT eval: document-level delegated listeners read the
 * attribute string and run it through a small interpreter for the app
 * handler grammar:
 *
 *   statements:   f(a,b); g();  ·  return false  ·  if (cond) { ... }
 *                 cond ? f() : g()  ·  path = expr
 *   expressions:  'str' "str" 123 true false null  ·  [ ... ] { "k": v }
 *                 this  event  this.value  window.location.hash  bare.paths
 *                 f(args) (dotted callees keep their `this`)
 *                 === !== == != && || !x  ( ... )
 *
 * Anything outside the grammar throws (console.warn) — the
 * scripts/check-csp-inline.py scanner keeps app surfaces inside it.
 *
 * Activation: only when CSP actually neuters inline handlers (probed by
 * checking whether an onclick attribute reflects to a function property).
 * On normal daemon pages the browser runs handlers natively and the bridge
 * stays inert, so double-firing is impossible.
 */
(function (root) {
  'use strict';

  // Constructs the grammar deliberately refuses — handlers using these must
  // move into real script files (the check-csp-inline.py scanner enforces).
  var RESERVED = [
    'var', 'let', 'const', 'function', 'while', 'for', 'do', 'switch',
    'new', 'delete', 'typeof', 'instanceof', 'in', 'of', 'class', 'try',
    'catch', 'finally', 'throw', 'yield', 'async', 'await', 'with', 'eval',
  ];

  // ── Tokenizer ──
  function tokenize(src) {
    var tokens = [];
    var i = 0, n = src.length;
    var punct3 = ['===', '!=='];
    var punct2 = ['&&', '||', '==', '!='];
    var punct1 = '(){}[],.;:?!=<>';
    while (i < n) {
      var c = src[i];
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { i++; continue; }
      if (c === "'" || c === '"') {
        var quote = c, val = '', j = i + 1;
        while (j < n) {
          if (src[j] === '\\' && j + 1 < n) { val += src[j + 1]; j += 2; continue; }
          if (src[j] === quote) break;
          val += src[j]; j++;
        }
        if (j >= n) throw new Error('unterminated string');
        tokens.push({ t: 'str', v: val });
        i = j + 1;
        continue;
      }
      if (c >= '0' && c <= '9') {
        var num = '', k = i;
        while (k < n && /[0-9.]/.test(src[k])) { num += src[k]; k++; }
        tokens.push({ t: 'num', v: parseFloat(num) });
        i = k;
        continue;
      }
      if (/[A-Za-z_$]/.test(c)) {
        var id = '', m = i;
        while (m < n && /[A-Za-z0-9_$]/.test(src[m])) { id += src[m]; m++; }
        if (RESERVED.indexOf(id) !== -1) {
          throw new Error('unsupported keyword ' + JSON.stringify(id));
        }
        tokens.push({ t: 'id', v: id });
        i = m;
        continue;
      }
      var three = src.slice(i, i + 3);
      if (punct3.indexOf(three) !== -1) { tokens.push({ t: 'p', v: three }); i += 3; continue; }
      var two = src.slice(i, i + 2);
      if (punct2.indexOf(two) !== -1) { tokens.push({ t: 'p', v: two }); i += 2; continue; }
      if (punct1.indexOf(c) !== -1) { tokens.push({ t: 'p', v: c }); i++; continue; }
      throw new Error('unexpected char ' + JSON.stringify(c));
    }
    return tokens;
  }

  // ── Parser → tiny AST ──
  function Parser(tokens) { this.toks = tokens; this.i = 0; }
  Parser.prototype.peek = function (o) { return this.toks[this.i + (o || 0)]; };
  Parser.prototype.next = function () { return this.toks[this.i++]; };
  Parser.prototype.eat = function (t, v) {
    var tok = this.peek();
    if (!tok || tok.t !== t || (v !== undefined && tok.v !== v)) {
      throw new Error('expected ' + (v || t) + ' got ' + JSON.stringify(tok));
    }
    return this.next();
  };
  Parser.prototype.at = function (t, v) {
    var tok = this.peek();
    return !!tok && tok.t === t && (v === undefined || tok.v === v);
  };

  Parser.prototype.program = function (stopAtBrace) {
    var stmts = [];
    while (this.peek()) {
      if (stopAtBrace && this.at('p', '}')) break;
      if (this.at('p', ';')) { this.next(); continue; }
      stmts.push(this.statement());
    }
    return { k: 'seq', body: stmts };
  };

  Parser.prototype.statement = function () {
    if (this.at('id', 'return')) {
      this.next();
      var val = null;
      if (this.peek() && !this.at('p', ';') && !this.at('p', '}')) val = this.expr();
      return { k: 'return', value: val };
    }
    if (this.at('id', 'if')) {
      this.next();
      this.eat('p', '(');
      var cond = this.expr();
      this.eat('p', ')');
      var body = this.blockOrStatement();
      var alt = null;
      if (this.at('id', 'else')) {
        this.next();
        alt = this.blockOrStatement();
      }
      return { k: 'if', cond: cond, body: body, alt: alt };
    }
    var e = this.expr();
    // Assignment: path = expr
    if (this.at('p', '=')) {
      if (e.k !== 'path') throw new Error('can only assign to a path');
      this.next();
      return { k: 'assign', target: e, value: this.expr() };
    }
    return { k: 'expr', expr: e };
  };

  // `{ ...statements }` or a single brace-less statement (the common
  // `if(cond) doThing()` handler idiom).
  Parser.prototype.blockOrStatement = function () {
    if (this.at('p', '{')) {
      this.next();
      var body = this.program(true);
      this.eat('p', '}');
      return body;
    }
    return { k: 'seq', body: [this.statement()] };
  };

  Parser.prototype.expr = function () { return this.ternary(); };
  Parser.prototype.ternary = function () {
    var cond = this.or();
    if (this.at('p', '?')) {
      this.next();
      var a = this.expr();
      this.eat('p', ':');
      var b = this.expr();
      return { k: 'ternary', cond: cond, a: a, b: b };
    }
    return cond;
  };
  Parser.prototype.or = function () {
    var l = this.and();
    while (this.at('p', '||')) { this.next(); l = { k: 'or', l: l, r: this.and() }; }
    return l;
  };
  Parser.prototype.and = function () {
    var l = this.eq();
    while (this.at('p', '&&')) { this.next(); l = { k: 'and', l: l, r: this.eq() }; }
    return l;
  };
  Parser.prototype.eq = function () {
    var l = this.unary();
    while (this.at('p', '===') || this.at('p', '!==') || this.at('p', '==') || this.at('p', '!=')) {
      var op = this.next().v;
      l = { k: 'eq', op: op, l: l, r: this.unary() };
    }
    return l;
  };
  Parser.prototype.unary = function () {
    if (this.at('p', '!')) { this.next(); return { k: 'not', e: this.unary() }; }
    return this.postfix();
  };

  Parser.prototype.postfix = function () {
    var e = this.primary();
    for (;;) {
      if (this.at('p', '.')) {
        this.next();
        var name = this.eat('id').v;
        if (e.k !== 'path') e = { k: 'path', base: e, parts: [name] };
        else e = { k: 'path', base: e.base, parts: e.parts.concat([name]) };
      } else if (this.at('p', '(')) {
        this.next();
        var args = [];
        if (!this.at('p', ')')) {
          args.push(this.expr());
          while (this.at('p', ',')) { this.next(); args.push(this.expr()); }
        }
        this.eat('p', ')');
        e = { k: 'call', callee: e, args: args };
      } else break;
    }
    return e;
  };

  Parser.prototype.primary = function () {
    var tok = this.peek();
    if (!tok) throw new Error('unexpected end');
    if (tok.t === 'str') { this.next(); return { k: 'lit', v: tok.v }; }
    if (tok.t === 'num') { this.next(); return { k: 'lit', v: tok.v }; }
    if (tok.t === 'id') {
      if (tok.v === 'true') { this.next(); return { k: 'lit', v: true }; }
      if (tok.v === 'false') { this.next(); return { k: 'lit', v: false }; }
      if (tok.v === 'null') { this.next(); return { k: 'lit', v: null }; }
      if (tok.v === 'undefined') { this.next(); return { k: 'lit', v: undefined }; }
      this.next();
      return { k: 'path', base: null, parts: [tok.v] };
    }
    if (tok.t === 'p' && tok.v === '(') {
      this.next();
      var e = this.expr();
      this.eat('p', ')');
      return e;
    }
    if (tok.t === 'p' && tok.v === '[') {
      this.next();
      var arr = [];
      if (!this.at('p', ']')) {
        arr.push(this.expr());
        while (this.at('p', ',')) { this.next(); arr.push(this.expr()); }
      }
      this.eat('p', ']');
      return { k: 'arr', items: arr };
    }
    if (tok.t === 'p' && tok.v === '{') {
      this.next();
      var props = [];
      if (!this.at('p', '}')) {
        for (;;) {
          var key = this.next();
          if (key.t !== 'str' && key.t !== 'id') throw new Error('bad object key');
          this.eat('p', ':');
          props.push({ key: key.v, value: this.expr() });
          if (this.at('p', ',')) { this.next(); continue; }
          break;
        }
      }
      this.eat('p', '}');
      return { k: 'obj', props: props };
    }
    throw new Error('unexpected token ' + JSON.stringify(tok));
  };

  function parse(src) {
    return new Parser(tokenize(src)).program(false);
  }

  // ── Evaluator ──
  var RETURN = {};   // sentinel

  function evalNode(node, ctx) {
    switch (node.k) {
      case 'seq': {
        for (var i = 0; i < node.body.length; i++) {
          var r = evalNode(node.body[i], ctx);
          if (r === RETURN) return RETURN;
        }
        return undefined;
      }
      case 'return': {
        var v = node.value === null ? undefined : evalNode(node.value, ctx);
        if (v === false) ctx.returnFalse = true;
        return RETURN;
      }
      case 'if': {
        if (evalNode(node.cond, ctx)) return evalNode(node.body, ctx);
        if (node.alt) return evalNode(node.alt, ctx);
        return undefined;
      }
      case 'expr': { evalNode(node.expr, ctx); return undefined; }
      case 'assign': {
        var ref = resolvePath(node.target, ctx, true);
        ref.obj[ref.key] = evalNode(node.value, ctx);
        return undefined;
      }
      case 'lit': return node.v;
      case 'arr': return node.items.map(function (x) { return evalNode(x, ctx); });
      case 'obj': {
        var o = {};
        node.props.forEach(function (p) { o[p.key] = evalNode(p.value, ctx); });
        return o;
      }
      case 'ternary': return evalNode(node.cond, ctx) ? evalNode(node.a, ctx) : evalNode(node.b, ctx);
      case 'or': return evalNode(node.l, ctx) || evalNode(node.r, ctx);
      case 'and': return evalNode(node.l, ctx) && evalNode(node.r, ctx);
      case 'not': return !evalNode(node.e, ctx);
      case 'eq': {
        var a = evalNode(node.l, ctx), b = evalNode(node.r, ctx);
        switch (node.op) {
          case '===': return a === b;
          case '!==': return a !== b;
          case '==': return a == b;   // eslint-disable-line eqeqeq
          case '!=': return a != b;   // eslint-disable-line eqeqeq
        }
        return false;
      }
      case 'path': {
        var ref2 = resolvePath(node, ctx, false);
        return ref2.obj == null ? undefined : ref2.obj[ref2.key];
      }
      case 'call': {
        var callee = node.callee;
        if (callee.k !== 'path') throw new Error('can only call paths');
        var ref3 = resolvePath(callee, ctx, false);
        var fn = ref3.obj == null ? undefined : ref3.obj[ref3.key];
        if (typeof fn !== 'function') throw new Error('not a function: ' + callee.parts.join('.'));
        var args = node.args.map(function (x) { return evalNode(x, ctx); });
        return fn.apply(ref3.obj, args);
      }
    }
    throw new Error('bad node ' + node.k);
  }

  // Resolve a path node to {obj, key} — obj[key] is the value / call target.
  function resolvePath(node, ctx, forWrite) {
    var parts = node.parts;
    var rootObj;
    var start = 0;
    var first = parts[0];
    if (node.base) {
      rootObj = evalNode(node.base, ctx);
      start = 0;
    } else if (first === 'this') { rootObj = ctx.el; start = 1; }
    else if (first === 'event') { rootObj = ctx.event; start = 1; }
    else if (first === 'window') { rootObj = ctx.global; start = 1; }
    else { rootObj = ctx.global; start = 0; }
    if (start >= parts.length) {
      // Bare `this` / `event` / `window` as a value.
      return { obj: { v: rootObj }, key: 'v' };
    }
    var obj = rootObj;
    for (var i = start; i < parts.length - 1; i++) {
      if (obj == null) throw new Error('cannot read ' + parts[i] + ' of null');
      obj = obj[parts[i]];
    }
    if (obj == null && !forWrite) return { obj: null, key: parts[parts.length - 1] };
    return { obj: obj, key: parts[parts.length - 1] };
  }

  function run(src, el, event, globalObj) {
    var ctx = { el: el, event: event, global: globalObj || root, returnFalse: false };
    evalNode(parse(src), ctx);
    return ctx;
  }

  var api = { parse: parse, run: run };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.EOS_CSP_BRIDGE = api;

  // ── DOM wiring (browsers only, and only when CSP neuters inline handlers) ──
  if (typeof document === 'undefined') return;

  function cspBlocksInline() {
    try {
      var t = document.createElement('div');
      t.setAttribute('onclick', 'void 0');
      return typeof t.onclick !== 'function';
    } catch (_) { return true; }
  }

  function install() {
    if (!cspBlocksInline()) return;   // native handlers work — stay inert
    var TYPES = ['click', 'change', 'input', 'keydown', 'submit'];
    TYPES.forEach(function (type) {
      document.addEventListener(type, function (event) {
        var attr = 'on' + type;
        var el = event.target;
        var stopped = false;
        var realStop = event.stopPropagation.bind(event);
        event.stopPropagation = function () { stopped = true; realStop(); };
        while (el && el !== document) {
          if (el.hasAttribute && el.hasAttribute(attr)) {
            var src = el.getAttribute(attr);
            try {
              var ctx = run(src, el, event, window);
              if (ctx.returnFalse) event.preventDefault();
            } catch (e) {
              console.warn('[eos-csp-bridge]', attr, 'failed:', e.message, '—', src);
            }
            if (stopped) break;
          }
          el = el.parentNode;
        }
      });
    });
    // blur doesn't bubble — delegate with capture.
    document.addEventListener('blur', function (event) {
      var el = event.target;
      if (el && el.hasAttribute && el.hasAttribute('onblur')) {
        try { run(el.getAttribute('onblur'), el, event, window); }
        catch (e) { console.warn('[eos-csp-bridge] onblur failed:', e.message); }
      }
    }, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})(typeof window !== 'undefined' ? window : globalThis);
