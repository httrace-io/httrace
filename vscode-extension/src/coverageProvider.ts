/**
 * coverageProvider.ts
 *
 * Fetches endpoint coverage data from the Httrace API and caches it.
 * The cache is invalidated when a new fetch succeeds or when the configuration
 * changes. All fetch errors are reported back to the caller — the extension
 * decides whether to surface them as UI messages.
 */

import * as https from "https";
import * as http from "http";
import { URL } from "url";

export interface EndpointCoverage {
  /** HTTP method, e.g. "GET" */
  method: string;
  /** URL path, e.g. "/api/orders" */
  path: string;
  /** Total number of captures stored for this endpoint */
  captures: number;
  /** All distinct HTTP status codes observed */
  statuses: number[];
}

export interface CoverageResult {
  service: string;
  endpoints: EndpointCoverage[];
  total_captures: number;
  fetchedAt: Date;
}

export class CoverageProvider {
  private _cache: CoverageResult | null = null;

  /**
   * Fetch coverage from the Httrace API.
   *
   * @param apiUrl  Base URL of the API, e.g. "https://api.httrace.com"
   * @param apiKey  Httrace API key (ht_…)
   * @param service Service name to query coverage for
   * @returns Resolved CoverageResult on success
   * @throws Error with a human-readable message on network or API failure
   */
  async fetchCoverage(
    apiUrl: string,
    apiKey: string,
    service: string
  ): Promise<CoverageResult> {
    const raw = await this._get(apiUrl, "/v1/coverage", { service }, apiKey);
    const result: CoverageResult = {
      service: raw.service,
      endpoints: raw.endpoints as EndpointCoverage[],
      total_captures: raw.total_captures,
      fetchedAt: new Date(),
    };
    this._cache = result;
    return result;
  }

  /** Return the last successfully fetched result without making a network call. */
  get cached(): CoverageResult | null {
    return this._cache;
  }

  /** Discard any cached data (e.g. after a config change). */
  invalidate(): void {
    this._cache = null;
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private _get(
    baseUrl: string,
    pathname: string,
    query: Record<string, string>,
    apiKey: string
  ): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      let url: URL;
      try {
        url = new URL(pathname, baseUrl);
      } catch {
        reject(new Error(`Invalid Httrace API URL: ${baseUrl}`));
        return;
      }
      Object.entries(query).forEach(([k, v]) => url.searchParams.set(k, v));

      const isHttps = url.protocol === "https:";
      const transport = isHttps ? https : http;
      const options = {
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname + url.search,
        method: "GET",
        headers: {
          "x-api-key": apiKey,
          "Accept": "application/json",
          "User-Agent": "httrace-vscode/0.1.0",
        },
      };

      const req = transport.request(options, (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk: string) => { body += chunk; });
        res.on("end", () => {
          if (!res.statusCode || res.statusCode >= 400) {
            reject(
              new Error(
                `Httrace API returned ${res.statusCode}: ${body.slice(0, 200)}`
              )
            );
            return;
          }
          try {
            resolve(JSON.parse(body) as Record<string, unknown>);
          } catch {
            reject(new Error("Httrace API returned invalid JSON"));
          }
        });
      });

      req.on("error", (err: Error) => {
        reject(new Error(`Network error reaching Httrace API: ${err.message}`));
      });

      req.setTimeout(8000, () => {
        req.destroy();
        reject(new Error("Httrace API request timed out after 8 s"));
      });

      req.end();
    });
  }
}
