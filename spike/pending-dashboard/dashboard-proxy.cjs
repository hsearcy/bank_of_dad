// NEVER DEPLOY: loopback-only dashboard ingress. Test controls are unreachable here.
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');
const pathModule = require('node:path');
const upstream = Number(process.env.INTERNAL_PORT);
// Assets are opt-in and read only from the checked-out template and static files.
const root = process.env.SYNTHETIC_UI_ROOT;
function forwarded(req) {
  const url = new URL(req.url, 'http://127.0.0.1');
  const headers = {...req.headers,host:`127.0.0.1:${upstream}`};
  if (url.pathname === '/__spike/socket') {
    // Keep fake socket credentials out of upstream request URL logs.
    const safeHeader = value => /^[\x20-\x7e]{1,1024}$/.test(value || '') ? value : '';
    headers['x-spike-family'] = safeHeader(url.searchParams.get('family'));
    headers['x-spike-token'] = safeHeader(url.searchParams.get('token'));
    return {path:url.pathname, headers};
  }
  return {path:req.url, headers};
}
const server = http.createServer((req,res) => {
  const path = new URL(req.url, 'http://127.0.0.1').pathname;
  if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
  if (root && (path === '/' || /^\/static\/[\w.-]+$/.test(path))) {
    if (path === '/static/sw.js') {res.writeHead(404); res.end(); return;}
    const file = path === '/' ? pathModule.join(root,'templates/index.html') :
      path === '/static/synthetic.js' ? pathModule.join(root,'spike/pending-dashboard/synthetic.js') : pathModule.join(root,path.slice(1));
    try {
      let data = fs.readFileSync(file);
      if (path === '/') {
        const source = data.toString();
        const needle = '<script src="/static/app.js">';
        if (!source.includes(needle)) throw Error('dashboard script missing');
        data = source.replace(needle,'<script src="/static/synthetic.js"></script>' + needle);
      }
      const type = path === '/' ? 'text/html' : path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'application/octet-stream';
      res.writeHead(200,{'content-type':type,'cache-control':'no-store','x-content-type-options':'nosniff'}); res.end(data);
    } catch (_) {res.writeHead(404); res.end();}
    return;
  }
  if (!['/api/state','/api/transactions','/api/pending','/__spike/socket'].includes(path)) {
    res.writeHead(404); res.end(); return;
  }
  const {path: target, headers} = forwarded(req);
  const proxy = http.request({host:'127.0.0.1',port:upstream,path:target,method:req.method,headers}, r => {res.writeHead(r.statusCode,r.headers); r.pipe(res);});
  proxy.on('error',()=>{if (!res.headersSent) res.writeHead(502); res.end();});
  req.pipe(proxy);
});
server.on('upgrade',(req,socket,head)=>{
  if (req.method !== 'GET' || !req.url.startsWith('/__spike/socket?')) {socket.destroy(); return;}
  const dest=net.connect(upstream,'127.0.0.1',()=>{
    const {path: target, headers}=forwarded(req);
    dest.write(`${req.method} ${target} HTTP/${req.httpVersion}\r\n`+Object.entries(headers).map(([k,v])=>`${k}: ${v}\r\n`).join('')+'\r\n');
    if (head.length) dest.write(head); socket.pipe(dest).pipe(socket);
  });
  dest.on('error',()=>socket.destroy()); socket.on('error',()=>dest.destroy());
});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
