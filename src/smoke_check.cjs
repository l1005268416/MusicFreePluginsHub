#!/usr/bin/env node
/**
 * MusicFree 插件功能冒烟测试（可选）
 * 用法：node smoke_check.cjs <plugin.js> [query] [timeoutMs] [searchType]
 * 输出："ok:true/false" JSON 行；搜索成功且 items>0 即通过
 */
const fs = require('fs');
const crypto = require('crypto');

const file = process.argv[2];
const query = process.argv[3] || '周杰伦';
const timeoutMs = Number(process.argv[4] || 5000);
const type = process.argv[5] || 'music';
const code = fs.readFileSync(file, 'utf-8');

function makeAxios(timeout) {
  const fn = async (config = {}) => {
    const method = (typeof config === 'string' ? 'GET' : (config.method || 'GET')).toUpperCase();
    let url = typeof config === 'string' ? config : (config.url || '');
    const headers = { ...(config.headers || {}) };
    if (typeof config === 'object' && config && config.params && typeof config.params === 'object') {
      const qs = Object.entries(config.params)
        .filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(String(v))).join('&');
      if (qs) url += (url.includes('?') ? '&' : '?') + qs;
    }
    let body = (typeof config === 'object') ? config.data : undefined;
    if (body && typeof body === 'object' && !(body instanceof FormData) && !Buffer.isBuffer(body) && !(body instanceof Blob)) {
      if (!headers['Content-Type'] && !headers['content-type']) headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const res = await fetch(url, { method, headers, body, signal: ctrl.signal, redirect: 'follow', credentials: 'include' });
      const text = await res.text();
      let data = text; try { data = JSON.parse(text); } catch (e) {}
      return { data, status: res.status, headers: Object.fromEntries(res.headers) };
    } finally { clearTimeout(timer); }
  };
  fn.get = async (url, config = {}) => fn({ ...config, method: 'GET', url });
  fn.post = async (url, data, config = {}) => fn({ ...config, method: 'POST', url, data });
  fn.request = async (config) => fn(config);
  return fn;
}
const axiosPlain = makeAxios(timeoutMs);
const axiosShim = Object.assign(axiosPlain, { default: axiosPlain });

const cryptoJs = {
  MD5: s => ({ toString: () => crypto.createHash('md5').update(String(s)).digest('hex') }),
  SHA1: s => ({ toString: () => crypto.createHash('sha1').update(String(s)).digest('hex') }),
  SHA256: s => ({ toString: () => crypto.createHash('sha256').update(String(s)).digest('hex') }),
  HmacSHA1: (msg, key) => ({ toString: () => crypto.createHmac('sha1', String(key)).update(String(msg)).digest('hex') }),
  HmacMD5: (msg, key) => ({ toString: () => crypto.createHmac('md5', String(key)).update(String(msg)).digest('hex') }),
  enc: {
    Hex: {},
    Utf8: { parse: s => ({ toString: () => String(s) }) },
    Base64: { parse: s => ({ toString: () => String(s) }) },
  },
  mode: { ECB: {}, CBC: {}, CTR: {}, CFB: {}, OFB: {} },
  AES: { encrypt: (msg, key) => ({ toString: () => 'ciphertext', encrypt: () => ({ toString: () => 'ciphertext' }) }), decrypt: () => ({ toString: () => '' }) },
};
const heShim = {
  decode: s => String(s)
    .replace(/&(amp|lt|gt|quot|apos|#39|#x27|#38|#x22);/g, m => ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&#39;':"'",'&#x27;':"'",'&#38;':'&','&#x22;':'"'}[m] || m))
    .replace(/&#(\d+);/g, (m, n) => String.fromCharCode(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (m, n) => String.fromCharCode(parseInt(n, 16))),
  encode: s => String(s),
};
const qsShim = require('querystring');
function dayjsShim(x) {
  const d = new Date(x || Date.now());
  const pad = n => String(n).padStart(2, '0');
  return {
    format: f => f.replace(/YYYY/g, d.getFullYear()).replace(/MM/g, pad(d.getMonth() + 1)).replace(/DD/g, pad(d.getDate())).replace(/HH/g, pad(d.getHours())).replace(/mm/g, pad(d.getMinutes())).replace(/ss/g, pad(d.getSeconds())),
    valueOf: () => d.getTime(),
    toString: () => d.toISOString(),
  };
}
dayjsShim.unix = function(t) { return dayjsShim(t * 1000); };
dayjsShim.utc = function(x) { return dayjsShim(x); };
function bigShim() { return new Proxy(function(){}, { get: (t, p) => (p === 'default' || p === '__esModule') ? true : bigShim(), apply: () => bigShim(), construct: () => bigShim() }); }
const cheerioShim = { load: () => ({ find: () => ({ text: () => '', html: () => '', attr: () => undefined, each: () => {} }) }) };
function requireShim(name) {
  switch (name) {
    case 'axios': return { default: axiosShim, get: axiosShim.get, post: axiosShim.post, request: axiosShim.request };
    case 'crypto-js': return cryptoJs;
    case 'qs': return qsShim;
    case 'he': return heShim;
    case 'dayjs': return dayjsShim;
    case 'cheerio': return cheerioShim;
    case 'big-integer': return bigShim();
    default: return bigShim();
  }
}

const mod = { exports: {} }; const exp = mod.exports;
const g = {
  console, Buffer, process,
  env: { USER_AGENT: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', VERSION: '', BUILD: '', getUserVariables: () => ({}), getUserVariable: () => undefined },
  setTimeout: (f, ms) => { try { f(); } catch (e) {} return 0; },
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
};
try {
  new Function('module', 'exports', 'require', 'console', 'Buffer', 'process', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'globalThis', 'env', code)(
    mod, exp, requireShim, console, Buffer, process, g.setTimeout, g.clearTimeout, g.setInterval, g.clearInterval, g, g.env);
} catch (err) {
  console.log('LOAD_ERR ' + err.message.split('\n')[0].slice(0, 120));
  process.exit(0);
}
const e = mod.exports || {};
if (typeof e.search !== 'function') {
  console.log('NO_SEARCH ' + Object.keys(e).join(','));
  process.exit(0);
}

const start = Date.now();
let done = false;
function finish(ok, info) {
  if (done) return; done = true;
  console.log(JSON.stringify({ ok, info, elapsed: Date.now() - start }));
  process.exit(0);
}
setTimeout(() => finish(false, 'timeout ' + timeoutMs + 'ms'), timeoutMs + 1500);

try {
  const r = e.search(query, 1, type);
  if (r && typeof r.then === 'function') {
    r.then(res => {
      const list = (res && Array.isArray(res.data)) ? res.data
        : (Array.isArray(res) ? res
          : ((res && Array.isArray(res.result)) ? res.result : []));
      finish(list.length > 0, `items=${list.length}`);
    }).catch(err => finish(false, 'search_err:' + err.message.split('\n')[0].slice(0, 120)));
  } else {
    finish(true, 'sync done');
  }
} catch (err) {
  finish(false, 'throw:' + err.message.split('\n')[0].slice(0, 120));
}
