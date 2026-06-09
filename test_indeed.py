#!/home/thehunter/venv/bin/python3
"""
Script de découverte Indeed MCP.

Teste la connexion au serveur Indeed MCP, découvre les outils disponibles
et valide la logique Remote → Hybrid à deux passes.

Usage:
    export INDEED_MCP_API_KEY=<clé>
    export INDEED_MCP_URL=https://mcp.indeed.com/mcp   # optionnel
    /home/thehunter/venv/bin/python3 /opt/apps/job-hunter/test_indeed.py

Sortie:
    Phases numérotées + rapport JSON final pour documenter indeed_mcp_findings.md.
    Exit 0 = tout OK, 1 = au moins un échec.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    import urllib.request
    import urllib.error

# ─── Configuration ─────────────────────────────────────────────────────────────

INDEED_MCP_API_KEY: str = os.environ.get("INDEED_MCP_API_KEY", "")
INDEED_MCP_URL: str = os.environ.get("INDEED_MCP_URL", "https://mcp.indeed.com/mcp")

SCAN_ROOTS: list[str] = [
    "AI", "agent", "agentic", "GenAI", "automation",
    "LLM", "RAG", "ML", "full stack", "n8n", "Python", "developer",
]

HYBRID_THRESHOLD: int = 10
GLOBAL_CAP: int = 60
PER_ROOT_LIMIT: int = 30  # max results per root per pass

# URLs à essayer si INDEED_MCP_URL ne répond pas
_CANDIDATE_URLS: list[str] = [
    INDEED_MCP_URL,
    "https://mcp.indeed.com/mcp",
    "https://mcp.indeed.com/",
    "https://mcp.indeed.com/v1/mcp",
    "https://api.indeed.com/mcp",
]

# Noms de tool courants pour la recherche d'emploi
_CANDIDATE_TOOL_NAMES: list[str] = [
    "search_jobs",
    "find_jobs",
    "job_search",
    "searchJobs",
    "jobs_search",
    "jobSearch",
]

# ─── MCP Client HTTP ───────────────────────────────────────────────────────────


class MCPError(Exception):
    """Erreur retournée par le serveur MCP."""


class MCPClient:
    """Client MCP JSON-RPC 2.0 over HTTP (streamable HTTP transport)."""

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key
        self._req_id: int = 0
        self._session_id: str | None = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
            # Certains serveurs utilisent x-api-key plutôt que Authorization
            h["X-Api-Key"] = self.api_key
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, payload: dict) -> dict:
        """Envoie une requête JSON-RPC, retourne result ou lève MCPError."""
        body = json.dumps(payload)
        if _HAS_REQUESTS:
            resp = _requests.post(
                self.url,
                data=body,
                headers=self._headers(),
                timeout=30,
            )
            if "Mcp-Session-Id" in resp.headers:
                self._session_id = resp.headers["Mcp-Session-Id"]
            try:
                resp.raise_for_status()
            except Exception as exc:
                raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}") from exc
            try:
                data = resp.json()
            except Exception as exc:
                raise MCPError(f"Invalid JSON response: {resp.text[:200]}") from exc
        else:
            req = urllib.request.Request(
                self.url,
                data=body.encode(),
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
            except urllib.error.HTTPError as exc:
                raise MCPError(f"HTTP {exc.code}: {exc.reason}") from exc

        if "error" in data:
            raise MCPError(f"MCP error: {json.dumps(data['error'])}")
        return data.get("result", {})

    def initialize(self) -> dict:
        """Handshake MCP (obligatoire avant tout appel)."""
        return self._post({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "job-hunter-discovery", "version": "1.0"},
            },
            "id": self._next_id(),
        })

    def list_tools(self) -> list[dict]:
        """Retourne la liste des outils disponibles avec leurs schémas."""
        result = self._post({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": self._next_id(),
        })
        return result.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Appelle un outil MCP avec les arguments fournis."""
        result = self._post({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": self._next_id(),
        })
        # Le résultat MCP est dans content[0].text (texte JSON ou raw)
        content = result.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except Exception:
                return text
        return result


# ─── Découverte URL ─────────────────────────────────────────────────────────────


def discover_working_url(api_key: str) -> tuple[str | None, dict]:
    """Essaie les URLs candidates, retourne (url_fonctionnelle, init_result)."""
    seen: set[str] = set()
    for url in _CANDIDATE_URLS:
        if url in seen:
            continue
        seen.add(url)
        print(f"  Trying {url} ...", end=" ", flush=True)
        try:
            client = MCPClient(url, api_key)
            result = client.initialize()
            info = result.get("serverInfo", {})
            print(f"OK — server={info.get('name','?')} v{info.get('version','?')}")
            return url, result
        except Exception as exc:
            print(f"FAIL ({exc})")
    return None, {}


# ─── Découverte outil ──────────────────────────────────────────────────────────


def discover_tool(client: MCPClient) -> tuple[str | None, list[dict]]:
    """Appelle tools/list, retourne (nom_outil_recherche, liste_tous_outils)."""
    try:
        tools = client.list_tools()
    except Exception as exc:
        print(f"  tools/list FAIL: {exc}")
        return None, []

    print(f"  {len(tools)} outil(s) découvert(s):")
    for t in tools:
        schema = t.get("inputSchema", {})
        props = list(schema.get("properties", {}).keys())
        req = schema.get("required", [])
        print(f"    • {t['name']}")
        print(f"      Description : {t.get('description', '(aucune)')}")
        for p in props:
            info = schema["properties"][p]
            flag = "[required]" if p in req else "[optional]"
            print(f"      - {p} ({info.get('type','?')}) {flag}: {info.get('description','')}")

    for t in tools:
        if any(kw in t["name"].lower() for kw in ("search", "find", "job")):
            return t["name"], tools
    if tools:
        return tools[0]["name"], tools
    return None, []


# ─── Appel de recherche ─────────────────────────────────────────────────────────

# Paramètres candidats pour le filtre remote / hybrid.
# Le script tente chaque combinaison jusqu'au premier succès.
_REMOTE_FILTERS: list[dict] = [
    {"remoteJobsOnly": True},
    {"remote": True},
    {"remote": "true"},
    {"remote": 1},
    {"f_WT": "2"},            # Indeed paramètre interne (remote)
    {"remotejob": "true"},
    {"work_from_home": True},
]
_HYBRID_FILTERS: list[dict] = [
    {"remoteJobsOnly": False, "hybridOk": True},
    {"remote": False},
    {"f_WT": "3"},             # Indeed paramètre interne (hybrid)
    {"work_type": "hybrid"},
    {},                         # pas de filtre = inclut hybrid
]
_QUERY_PARAM_NAMES: list[str] = ["query", "q", "keywords", "searchTerm", "what", "title"]
_LIMIT_PARAM_NAMES: list[str] = ["limit", "maxResults", "count", "num", "resultsPerPage"]


def _make_args(
    query: str, remote_only: bool, limit: int, qpname: str, lpname: str, filter_args: dict
) -> dict:
    return {qpname: query, lpname: limit, **filter_args}


def search_jobs(
    client: MCPClient,
    tool_name: str,
    query: str,
    remote_only: bool,
    limit: int,
) -> tuple[list[dict], dict]:
    """
    Appelle l'outil MCP, retourne (offres_brutes, params_qui_ont_fonctionné).
    Essaie plusieurs combinaisons de noms de paramètres.
    """
    filters = _REMOTE_FILTERS if remote_only else _HYBRID_FILTERS

    for qpname in _QUERY_PARAM_NAMES:
        for lpname in _LIMIT_PARAM_NAMES:
            for fargs in filters:
                args = _make_args(query, remote_only, limit, qpname, lpname, fargs)
                try:
                    raw = client.call_tool(tool_name, args)
                    offers = _extract_offers(raw)
                    if offers is not None:
                        return offers, {"query_param": qpname, "limit_param": lpname, "filter": fargs}
                except MCPError:
                    continue
                except Exception:
                    continue

    # Dernier recours : args minimaux
    try:
        raw = client.call_tool(tool_name, {"q": query, "limit": limit})
        offers = _extract_offers(raw)
        if offers is not None:
            return offers, {"query_param": "q", "limit_param": "limit", "filter": {}}
    except Exception:
        pass

    return [], {}


def _extract_offers(result: Any) -> list[dict] | None:
    """Extrait la liste d'offres depuis la réponse brute du tool."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("jobs", "results", "items", "offers", "data", "jobResults", "hits"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return None


def normalize_offer(raw: dict) -> dict:
    """Normalise une offre brute vers le format canonique du projet."""
    def _get(*keys: str, default: str = "") -> str:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return str(v)
        return default

    return {
        "url": _get("url", "link", "jobUrl", "applyUrl", "detailsPageUrl", "externalUrl"),
        "title": _get("title", "jobTitle", "name", "position"),
        "company": _get("company", "companyName", "employer", "employerName", "hiringOrganization"),
        "location": _get("location", "formattedLocation", "city", "jobLocation"),
        "description": _get("description", "snippet", "summary", "jobDescription", "body")[:500],
        "job_type": _get("jobType", "job_type", "employmentType", "workType", "contractType"),
        "date_posted": _get("datePosted", "date_posted", "postedAt", "formattedRelativeTime", "publishedAt"),
        "source": "indeed",
    }


# ─── Logique deux passes ────────────────────────────────────────────────────────


def run_two_pass_scan(
    client: MCPClient, tool_name: str
) -> tuple[list[dict], dict]:
    """
    Passe 1 (Remote) → si unique URLs < HYBRID_THRESHOLD, Passe 2 (Hybrid).
    Retourne (offres_normalisées_cap60, rapport_découverte).
    """
    all_offers: list[dict] = []
    seen_urls: set[str] = set()
    working_params: dict = {}

    # ── Passe 1 : Remote ──────────────────────────────────────────────────────
    print(f"\n--- Passe 1 : Remote ({len(SCAN_ROOTS)} racines) ---")
    for root in SCAN_ROOTS:
        remaining = GLOBAL_CAP - len(seen_urls)
        if remaining <= 0:
            print(f"  → Cap {GLOBAL_CAP} atteint, arrêt passe 1")
            break
        limit = min(PER_ROOT_LIMIT, remaining)
        print(f"  [{root}] ...", end=" ", flush=True)
        try:
            raw, params = search_jobs(client, tool_name, root, remote_only=True, limit=limit)
            new_count = 0
            for offer in raw:
                norm = normalize_offer(offer)
                url = norm["url"]
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_offers.append({**norm, "_pass": 1})
                    new_count += 1
            print(f"{new_count} nouvelles ({len(seen_urls)} total)")
            if params and not working_params:
                working_params = {**params, "discovered_on_root": root, "pass": "remote"}
        except Exception as exc:
            print(f"ERREUR: {exc}")
        time.sleep(0.5)  # politesse API

    remote_count = len(seen_urls)
    print(f"\nPasse 1 terminée : {remote_count} URLs uniques")

    # ── Passe 2 : Hybrid (seuil hardcodé = HYBRID_THRESHOLD) ─────────────────
    if remote_count < HYBRID_THRESHOLD:
        print(f"\n--- Passe 2 : Hybrid (remote={remote_count} < seuil={HYBRID_THRESHOLD}) ---")
        for root in SCAN_ROOTS:
            remaining = GLOBAL_CAP - len(seen_urls)
            if remaining <= 0:
                print(f"  → Cap {GLOBAL_CAP} atteint, arrêt passe 2")
                break
            limit = min(PER_ROOT_LIMIT, remaining)
            print(f"  [{root}] ...", end=" ", flush=True)
            try:
                raw, params = search_jobs(client, tool_name, root, remote_only=False, limit=limit)
                new_count = 0
                for offer in raw:
                    norm = normalize_offer(offer)
                    url = norm["url"]
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_offers.append({**norm, "_pass": 2})
                        new_count += 1
                print(f"{new_count} nouvelles ({len(seen_urls)} total)")
            except Exception as exc:
                print(f"ERREUR: {exc}")
            time.sleep(0.5)
    else:
        print(f"\nPasse 2 ignorée : remote={remote_count} ≥ seuil={HYBRID_THRESHOLD}")

    return all_offers[:GLOBAL_CAP], working_params


# ─── Rapport ────────────────────────────────────────────────────────────────────


def print_findings_report(
    url: str,
    init_result: dict,
    tools: list[dict],
    tool_name: str | None,
    working_params: dict,
    offers: list[dict],
) -> None:
    """Imprime un rapport formaté pour copier-coller dans indeed_mcp_findings.md."""
    sep = "═" * 65
    print(f"\n{sep}")
    print("RAPPORT — INDEED MCP FINDINGS (copier → indeed_mcp_findings.md)")
    print(sep)
    print(f"\n## URL MCP fonctionnelle\n{url}")
    info = init_result.get("serverInfo", {})
    print(f"\n## Server info\n- name: {info.get('name', '?')}")
    print(f"- version: {info.get('version', '?')}")
    print(f"- protocolVersion: {init_result.get('protocolVersion', '?')}")
    print(f"\n## Outils disponibles ({len(tools)})")
    for t in tools:
        schema = t.get("inputSchema", {})
        props = schema.get("properties", {})
        req = schema.get("required", [])
        print(f"\n### {t['name']}")
        print(f"Description: {t.get('description', '(aucune)')}")
        if props:
            print("Paramètres:")
            for p, info2 in props.items():
                flag = "required" if p in req else "optional"
                print(f"  - `{p}` ({info2.get('type','?')}) [{flag}]: {info2.get('description','')}")
    print(f"\n## Paramètres qui ont fonctionné")
    print(json.dumps(working_params, ensure_ascii=False, indent=2))
    print(f"\n## Résultats du scan")
    p1 = [o for o in offers if o.get("_pass") == 1]
    p2 = [o for o in offers if o.get("_pass") == 2]
    print(f"- Total offres : {len(offers)} (cap {GLOBAL_CAP})")
    print(f"- Passe 1 (Remote) : {len(p1)}")
    print(f"- Passe 2 (Hybrid) : {len(p2)}")
    if offers:
        print(f"\n## Exemple offre (première)")
        first = {k: v for k, v in offers[0].items() if not k.startswith("_")}
        for k, v in first.items():
            print(f"  - {k}: {str(v)[:80]}")
    print(f"\n{sep}")


# ─── Main ────────────────────────────────────────────────────────────────────────


def main() -> int:
    """Point d'entrée principal."""
    print("=" * 65)
    print("TEST INDEED MCP — Script de découverte et validation")
    print("=" * 65)

    # ── Phase 1 : Pré-vol ─────────────────────────────────────────────────────
    print("\n=== Phase 1 : Pré-vol ===")
    errors: list[str] = []

    if not INDEED_MCP_API_KEY:
        print("  ✗ INDEED_MCP_API_KEY non défini")
        print("  → export INDEED_MCP_API_KEY=<votre-clé>")
        errors.append("INDEED_MCP_API_KEY manquant")
    else:
        masked = "*" * 8 + INDEED_MCP_API_KEY[-4:]
        print(f"  ✓ API key : {masked}")

    print(f"  URL initiale : {INDEED_MCP_URL}")
    print(f"  Lib HTTP    : {'requests' if _HAS_REQUESTS else 'urllib'}")

    if errors:
        print("\n  ABORT: credentials manquants")
        return 1

    # ── Phase 2 : Découverte URL ───────────────────────────────────────────────
    print("\n=== Phase 2 : Découverte URL ===")
    working_url, init_result = discover_working_url(INDEED_MCP_API_KEY)
    if not working_url:
        print("  ✗ Aucune URL ne répond")
        print("  → Vérifier : clé valide ? accès réseau à mcp.indeed.com ?")
        return 1
    print(f"  ✓ URL fonctionnelle : {working_url}")
    client = MCPClient(working_url, INDEED_MCP_API_KEY)

    # ── Phase 3 : Découverte outil ─────────────────────────────────────────────
    print("\n=== Phase 3 : Découverte outils (tools/list) ===")
    tool_name, tools = discover_tool(client)

    if not tool_name:
        print("  tools/list n'a pas retourné d'outil de recherche")
        print("  → Essai des noms candidats...")
        for candidate in _CANDIDATE_TOOL_NAMES:
            print(f"  Trying {candidate} ...", end=" ", flush=True)
            try:
                raw = client.call_tool(candidate, {"q": "AI", "limit": 1})
                if raw is not None:
                    tool_name = candidate
                    print("OK")
                    break
                print("vide")
            except MCPError as exc:
                print(f"FAIL ({exc})")
            except Exception as exc:
                print(f"FAIL ({exc})")

    if not tool_name:
        print("  ✗ Aucun outil de recherche trouvé")
        return 1
    print(f"  ✓ Outil : {tool_name}")

    # ── Phase 4 : Test unitaire (1 racine, remote) ─────────────────────────────
    print("\n=== Phase 4 : Test unitaire (root='AI', remote=True, limit=5) ===")
    test_offers: list[dict] = []
    test_params: dict = {}
    try:
        raw, test_params = search_jobs(client, tool_name, "AI", remote_only=True, limit=5)
        test_offers = [normalize_offer(o) for o in raw[:5]]
        print(f"  ✓ {len(test_offers)} offre(s) retournée(s)")
        print(f"  ✓ Paramètres fonctionnels : {test_params}")

        required_fields = ["url", "title", "company", "description", "job_type"]
        if test_offers:
            missing = [f for f in required_fields if not test_offers[0].get(f)]
            if missing:
                print(f"  ⚠ Champs manquants dans la première offre : {missing}")
            else:
                print(f"  ✓ Tous les champs requis présents")
        else:
            print("  ⚠ Aucune offre retournée pour 'AI' remote")
    except Exception as exc:
        print(f"  ✗ FAIL: {exc}")
        return 1

    # ── Phase 5 : Scan complet deux passes ────────────────────────────────────
    print(f"\n=== Phase 5 : Scan complet ({len(SCAN_ROOTS)} racines, cap={GLOBAL_CAP}) ===")
    all_offers, working_params = run_two_pass_scan(client, tool_name)
    print(f"\n  ✓ Scan terminé : {len(all_offers)} offre(s) collectée(s)")

    # ── Phase 6 : Sortie JSON (3 premières offres) ────────────────────────────
    print("\n=== Phase 6 : Sortie JSON (3 premières offres) ===")
    preview = [{k: v for k, v in o.items() if not k.startswith("_")} for o in all_offers[:3]]
    print(json.dumps(preview, ensure_ascii=False, indent=2))

    # ── Phase 7 : Rapport findings ────────────────────────────────────────────
    print_findings_report(working_url, init_result, tools, tool_name, working_params, all_offers)

    # ── Résumé ────────────────────────────────────────────────────────────────
    print("\n=== Résumé ===")
    final_errors: list[str] = []
    if len(all_offers) == 0:
        final_errors.append("Aucune offre collectée")
    if test_offers and not test_offers[0].get("url"):
        final_errors.append("Champ 'url' absent dans les offres")

    if final_errors:
        for e in final_errors:
            print(f"  ✗ {e}")
        return 1

    print(f"  ✓ {len(all_offers)} offres collectées (cap {GLOBAL_CAP})")
    p1 = len([o for o in all_offers if o.get("_pass") == 1])
    p2 = len([o for o in all_offers if o.get("_pass") == 2])
    print(f"  ✓ Passe 1 (remote) : {p1} | Passe 2 (hybrid) : {p2}")
    print(f"  ✓ Zéro doublon (déduplication par URL)")
    print("  ✓ OK — Copier le RAPPORT ci-dessus dans indeed_mcp_findings.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
