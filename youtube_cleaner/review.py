"""The review page: a local web page (only reachable from this computer) that shows each video's before and after in
batches, lets the person edit the new text, and applies the checked ones."""
import http.server
import json
import secrets
import threading
import urllib.parse
import webbrowser

from . import engine, store

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube cleaner</title><style>
:root{--bg:#fff;--fg:#1d2433;--mut:#667085;--card:#f6f7fa;--line:#d9dde6;--acc:#2f5bea;--bad:#b42318;--del:#fde8e8;--add:#e3f5e8}
@media(prefers-color-scheme:dark){:root{--bg:#0f1522;--fg:#e6ebf5;--mut:#98a2b3;--card:#171f31;--line:#2a3550;--acc:#7c9bff;--bad:#ff8a80;--del:#3a1f24;--add:#17301f}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}main{max-width:980px;margin:0 auto;padding:16px}
header{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:2}
h1{font-size:18px;margin:0 0 4px}.mut{color:var(--mut);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0}
.row{display:flex;gap:10px;align-items:flex-start}.row label.t{flex:1}.b{background:var(--del);padding:2px 6px;border-radius:4px;word-break:break-word}
.a{width:100%;box-sizing:border-box;background:var(--add);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px;font:inherit}
textarea.a{min-height:110px}details{margin:6px 0}pre{white-space:pre-wrap;background:var(--del);padding:8px;border-radius:6px;margin:6px 0}
button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.p{background:var(--acc);color:#fff;border-color:var(--acc)}button:disabled{opacity:.5}.flag{color:var(--bad);font-size:13px}.err{color:var(--bad)}
a{color:var(--acc)}</style></head><body><main>
<header><h1 id="h">YouTube cleaner</h1><div class="mut" id="s"></div>
<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap"><button class="p" id="apply">Apply checked, skip the rest</button>
<button id="all">Check all</button><button id="none">Check none</button><button id="reload">Next batch</button></div><div id="msg" class="mut" style="margin-top:6px"></div></header>
<div id="list"></div>
<section id="done" style="margin-top:28px"><h2 style="font-size:16px;margin:0">Recently changed</h2>
<div class="mut">Videos this tool already changed on YouTube. Undo puts the original title and description back.</div><div id="dlist"></div></section>
<section id="skipped" style="margin:28px 0"><h2 style="font-size:16px;margin:0">Left alone or undone</h2>
<div class="mut">Videos you skipped or put back. Review again brings one back into the list at the top with its cleaned text.</div><div id="slist"></div></section></main><script>
const T=new URLSearchParams(location.search).get('t');const H={'X-Token':T,'Content-Type':'application/json'};
const $=id=>document.getElementById(id);const yt=id=>'https://www.youtube.com/watch?v='+encodeURIComponent(id);
function say(t,id){const m=$('msg');m.textContent=t;if(id){m.append(' ');const a=document.createElement('a');a.href=yt(id);a.target='_blank';a.rel='noopener';a.textContent='Open the video';m.append(a)}}let items=[];
async function api(p,o){const r=await fetch(p,Object.assign({headers:H},o));const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function state(){const s=await api('/api/state');$('h').textContent='YouTube cleaner: '+s.channel;
$('s').textContent=(s.counts.pending||0)+' to review · '+(s.counts.approved||0)+' approved, waiting for quota · '+(s.counts.applied||0)+' applied · '+(s.counts.same||0)+' already clean · '+(s.counts.error||0)+' with a problem · '+s.updatesLeft+' updates left today'}
async function load(){$('msg').textContent='';const j=await api('/api/batch');items=j.items;$('list').innerHTML=items.length?'':'<p>Nothing left to review.</p>';
items.forEach((it,i)=>{const d=document.createElement('div');d.className='card';
d.innerHTML=`<div class="row"><input type="checkbox" id="c${i}" checked style="margin-top:6px"><div style="flex:1">
<div class="mut">Title now</div><div class="b"><a href="${yt(it.id)}" target="_blank" rel="noopener">${esc(it.beforeTitle)}</a></div><div class="mut" style="margin-top:6px">Title after</div>
<input class="a" id="t${i}" value="${esc(it.afterTitle)}">
<details><summary class="mut">Description now</summary><pre>${esc(it.beforeDescription)||'(empty)'}</pre></details>
<div class="mut">Description after (you can edit it)</div><textarea class="a" id="d${i}">${esc(it.afterDescription)}</textarea>
${it.flags.length?`<div class="flag">Still sounds like a chatbot: ${esc(it.flags.join(', '))}. Reword it above if you like.</div>`:''}
${it.error?`<div class="err">Last try failed: ${esc(it.error)}</div>`:''}
<div class="mut"><a href="https://www.youtube.com/watch?v=${it.id}" target="_blank" rel="noopener">Open on YouTube</a></div></div></div>`;$('list').appendChild(d)});
await state()}
async function loadDone(){const j=await api('/api/applied');$('dlist').innerHTML=j.items.length?'':'<p class="mut">Nothing changed yet.</p>';
j.items.forEach(it=>{const d=document.createElement('div');d.className='card';
d.innerHTML=`<div class="mut">Title before</div><div class="b">${esc(it.beforeTitle)}</div><div class="mut" style="margin-top:6px">Title now</div><div class="b" style="background:var(--add)"><a href="${yt(it.id)}" target="_blank" rel="noopener">${esc(it.afterTitle)}</a></div>
<details><summary class="mut">Description before and now</summary><pre>${esc(it.beforeDescription)||'(empty)'}</pre><pre style="background:var(--add)">${esc(it.afterDescription)||'(empty)'}</pre></details>
<div style="display:flex;gap:12px;align-items:center;margin-top:6px"><a href="https://www.youtube.com/watch?v=${it.id}" target="_blank" rel="noopener">Open on YouTube</a><button class="u">Undo</button></div>`;
d.querySelector('.u').onclick=async e=>{if(!confirm('Put the original title and description back on YouTube?'))return;e.target.disabled=true;
try{const r=await api('/api/undo',{method:'POST',body:JSON.stringify({id:it.id})});say(r.restored?'Put back.':(r.failed.length?'Could not undo: '+r.failed[0][1]:(r.stopped?'YouTube\'s daily limit is used up. Try again after midnight Pacific time.':'Nothing to undo.')),r.restored?it.id:null);await loadDone();await loadSkipped();await state()}catch(x){$('msg').textContent=x.message;e.target.disabled=false}};
$('dlist').appendChild(d)})}
async function loadSkipped(){const j=await api('/api/skipped');$('slist').innerHTML=j.items.length?'':'<p class="mut">Nothing here.</p>';
j.items.forEach(it=>{const d=document.createElement('div');d.className='card';
d.innerHTML=`<div class="mut">Title now</div><div class="b"><a href="${yt(it.id)}" target="_blank" rel="noopener">${esc(it.beforeTitle)}</a></div><div class="mut" style="margin-top:6px">Cleaned title would be</div><div class="b" style="background:var(--add)">${esc(it.afterTitle)}</div>
<div style="display:flex;gap:12px;align-items:center;margin-top:6px"><a href="https://www.youtube.com/watch?v=${it.id}" target="_blank" rel="noopener">Open on YouTube</a><button class="r">Review again</button></div>`;
d.querySelector('.r').onclick=async e=>{e.target.disabled=true;try{await api('/api/requeue',{method:'POST',body:JSON.stringify({id:it.id})});say('Moved back to the review list.',it.id);await load();await loadSkipped()}catch(x){$('msg').textContent=x.message;e.target.disabled=false}};
$('slist').appendChild(d)})}
function setAll(v){items.forEach((_,i)=>$('c'+i).checked=v)}
$('all').onclick=()=>setAll(true);$('none').onclick=()=>setAll(false);$('reload').onclick=()=>load().catch(e=>$('msg').textContent=e.message);
$('apply').onclick=async()=>{const edits={},skip=[];items.forEach((it,i)=>{if($('c'+i).checked)edits[it.id]={title:$('t'+i).value,description:$('d'+i).value};else skip.push(it.id)});
const n=Object.keys(edits).length;if(n&&!confirm('Change '+n+' live video'+(n==1?'':'s')+' on YouTube now? The originals are backed up first, and you can undo.'))return;
$('apply').disabled=true;$('msg').textContent='Working…';
try{const r=await api('/api/apply',{method:'POST',body:JSON.stringify({edits,skip})});
$('msg').textContent='Changed '+r.applied+'.'+(r.failed.length?' '+r.failed.length+' failed: '+r.failed.map(f=>f[0]+' ('+f[1]+')').join('; '):'')+(r.stopped?' YouTube\'s daily limit is used up; '+r.left+' approved video(s) will be changed when you run review again after midnight Pacific time.':'');
await load();await loadDone();await loadSkipped();}catch(e){$('msg').textContent=e.message}finally{$('apply').disabled=false}};
load().catch(e=>$('msg').textContent=e.message);loadDone().catch(()=>{});loadSkipped().catch(()=>{});
</script></body></html>"""


def make_server(yt, st, channel_title, size=20, port=0):
    """(server, url). The page and its data are only served to this computer, with a secret in the address."""
    secret = secrets.token_urlsafe(16)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _ok(self, needs_token):
            host = self.headers.get("Host", "")
            if host.split(":")[0] not in ("127.0.0.1", "localhost"):   # blocks look-alike names pointing at this computer
                return False
            return not needs_token or self.headers.get("X-Token") == secret

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                if not self._ok(False) or urllib.parse.parse_qs(url.query).get("t", [""])[0] != secret:
                    return self._send(403, {"error": "Open the address the terminal printed"})
                return self._send(200, PAGE.encode(), "text/html")
            if not self._ok(True):
                return self._send(403, {"error": "Not allowed"})
            if url.path == "/api/state":
                return self._send(200, {"channel": channel_title, "counts": st.counts(), "updatesLeft": st.updates_left_today(),
                                        "quotaUsed": st.quota_used()})
            if url.path == "/api/batch":
                proposals = st.proposals()
                pending = [(v, p) for v, p in proposals.items() if p["status"] in ("pending", "error")][:size]
                return self._send(200, {"items": [dict(p, id=v) for v, p in pending]})
            if url.path == "/api/skipped":
                proposals = st.proposals()
                left = [dict(p, id=v) for v, p in proposals.items() if p["status"] == "skipped"]
                return self._send(200, {"items": left[:200]})
            if url.path == "/api/applied":
                proposals = st.proposals()
                done = [dict(p, id=v) for v, p in proposals.items() if p["status"] == "applied"]
                return self._send(200, {"items": list(reversed(done))})
            return self._send(404, {"error": "Not found"})

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path not in ("/api/apply", "/api/undo", "/api/requeue") or not self._ok(True):
                return self._send(403, {"error": "Not allowed"})
            if path == "/api/requeue":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    vid = json.loads(self.rfile.read(min(length, 10_000)).decode() or "{}").get("id")
                except (ValueError, TypeError, AttributeError):
                    return self._send(400, {"error": "Could not read that"})
                proposals = st.proposals()
                if not isinstance(vid, str) or vid not in proposals or proposals[vid]["status"] != "skipped":
                    return self._send(400, {"error": "Not a skipped video"})
                proposals[vid]["status"] = "pending"
                st.save_proposals(proposals)
                return self._send(200, {"ok": True})
            if path == "/api/undo":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    vid = json.loads(self.rfile.read(min(length, 10_000)).decode() or "{}").get("id")
                except (ValueError, TypeError, AttributeError):
                    return self._send(400, {"error": "Could not read that"})
                if not isinstance(vid, str) or vid not in st.proposals():
                    return self._send(400, {"error": "Unknown video"})
                return self._send(200, engine.undo(yt, st, [vid]))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, 5_000_000)).decode() or "{}")
                edits = body.get("edits") if isinstance(body.get("edits"), dict) else {}
                skip = [s for s in body.get("skip", []) if isinstance(s, str)]
                edits = {k: v for k, v in edits.items() if isinstance(v, dict)}
            except (ValueError, TypeError):
                return self._send(400, {"error": "Could not read that"})
            result = engine.apply(yt, st, edits, skip)
            return self._send(200, result)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, f"http://127.0.0.1:{server.server_address[1]}/?t={secret}"


def serve(yt, st, channel_title, size=20, port=0, open_browser=webbrowser.open, say=print):
    server, url = make_server(yt, st, channel_title, size, port)
    say(f"Review page: {url}")
    say("Leave this running while you review. Press Ctrl+C here when you are done.")
    threading.Timer(0.3, lambda: open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
