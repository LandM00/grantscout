// Cloudflare Worker: intermediario tra l'app GrantScout e l'API di
// GitHub, per il pulsante "Avvia scansione ora".
//
// Perche' questo file esiste: un Personal Access Token di GitHub non
// puo' restare nel codice del sito (repository pubblico) -- GitHub lo
// rileva appena viene pubblicato e lo revoca automaticamente in pochi
// secondi, anche se lo autorizzi tu dal blocco "push protection". Questo
// Worker tiene il token vero SOLO qui, in una variabile "Secret" di
// Cloudflare (mai nel repository, mai visibile pubblicamente), e il sito
// gli parla tramite una password condivisa a basso rischio.
//
// Setup: vedi SETUP.md, sezione 14 "Avvia scansione dall'app".
//
// Variabili da impostare in Cloudflare (Settings -> Variables and
// Secrets), entrambe come tipo "Secret":
//   GITHUB_TOKEN        fine-grained PAT con SOLO permesso Actions:
//                        Read and write su questo repository
//   SCAN_SHARED_SECRET  una password a piacere, condivisa con
//                        docs/scan-config.js (campo "sharedSecret")

const ALLOWED_ORIGIN = "https://landm00.github.io";
const GH_OWNER = "LandM00";
const GH_REPO = "grantscout";
const GH_WORKFLOW = "scan.yml";
const GH_REF = "main";

export default {
  async fetch(request, env) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Scan-Secret",
    };

    // Richiesta di preflight CORS del browser: rispondiamo e basta.
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders });
    }

    // La password condivisa non e' un vero segreto ad alto rischio (vedi
    // sopra), ma controllarla evita che chiunque trovi l'indirizzo del
    // Worker possa usarlo per lanciare scansioni a piacimento.
    const secret = request.headers.get("X-Scan-Secret") || "";
    if (!env.SCAN_SHARED_SECRET || secret !== env.SCAN_SHARED_SECRET) {
      return new Response(JSON.stringify({ error: "unauthorized" }), {
        status: 401,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (!env.GITHUB_TOKEN) {
      return new Response(JSON.stringify({ error: "GITHUB_TOKEN non configurato su questo Worker" }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const ghResp = await fetch(
      `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/${GH_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "grantscout-scan-trigger-worker",
        },
        body: JSON.stringify({ ref: GH_REF }),
      }
    );

    const bodyText = await ghResp.text();
    return new Response(
      JSON.stringify({ ok: ghResp.ok, status: ghResp.status, body: bodyText }),
      {
        status: ghResp.ok ? 200 : 502,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  },
};
