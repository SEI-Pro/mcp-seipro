// O que ficou no serviço mcp-seipro do Railway depois da migração para a VPS (2026-10-24).
// - Host mcp.seipro.io: quem ainda resolve o DNS antigo cai aqui; repassa tudo (método,
//   corpo, cabeçalhos, SSE) para a VPS pelo nome auxiliar mcp-seipro.claudinha.tech.
//   Proxy e não redirect: cliente MCP e POST /token não seguem redirect de forma confiável.
// - Outros Hosts (mcp-seipro-production.up.railway.app): 308 para https://mcp.seipro.io.
// - Healthcheck do Railway (Host healthcheck.railway.app): 200.
const http = require("http");
const https = require("https");
const DESTINO = "mcp-seipro.claudinha.tech";
const CANONICO = "mcp.seipro.io";
const agente = new https.Agent({ keepAlive: true });

http
  .createServer((req, res) => {
    const host = (req.headers.host || "").split(":")[0].toLowerCase();
    if (host === "healthcheck.railway.app") return res.writeHead(200).end("ok");
    if (host !== CANONICO) {
      res.writeHead(308, { Location: `https://${CANONICO}${req.url}`, "Cache-Control": "no-store" });
      return res.end();
    }
    const headers = { ...req.headers, host: DESTINO };
    const up = https.request(
      { host: DESTINO, port: 443, method: req.method, path: req.url, headers, agent: agente },
      (r) => {
        res.writeHead(r.statusCode, r.headers);
        r.pipe(res);
      }
    );
    up.on("error", (e) => {
      console.error("proxy:", e.message);
      if (!res.headersSent) res.writeHead(502).end("bad gateway");
      else res.destroy();
    });
    req.pipe(up);
    res.on("close", () => up.destroy());
  })
  .listen(Number(process.env.PORT) || 8080);
