/**
 * Inline coverage decorations.
 *
 * Scans the active editor for route/endpoint definitions and adds
 * "✓ N captures" or "○ no captures" after the matching line.
 *
 * Supported patterns (Python FastAPI/Flask, Node Express, Go net/http, Ruby Rails):
 *   @app.get("/path")             — FastAPI / Flask
 *   @router.post("/path")         — FastAPI router
 *   router.get("/path", ...)      — Express
 *   r.Handle("/path", ...)        — Go chi/mux
 *   get "/path", to: "..."        — Rails routes
 */
import * as vscode from 'vscode';
import { CoverageEndpoint } from '../api';

// Decorator types
const COVERED_TYPE = vscode.window.createTextEditorDecorationType({
  after: {
    color: '#22c55e99',
    margin: '0 0 0 16px',
    fontStyle: 'italic',
    fontSize: '11px',
  },
  isWholeLine: false,
  rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
});

const UNCOVERED_TYPE = vscode.window.createTextEditorDecorationType({
  after: {
    color: '#66666680',
    margin: '0 0 0 16px',
    fontStyle: 'italic',
    fontSize: '11px',
  },
  isWholeLine: false,
  rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
});

// Route extraction patterns  [method, path]
const ROUTE_PATTERNS: RegExp[] = [
  // Python: @app.get("/path") / @router.post("/path")
  /@(?:\w+)\.(?:get|post|put|patch|delete|options|head)\s*\(\s*["']([^"']+)["']/gi,
  // FastAPI APIRouter
  /(?:get|post|put|patch|delete)\s*\(\s*["']([^"']+)["']/gi,
  // Express: router.get('/path', ...)
  /(?:router|app|server)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*["']([^"']+)["']/gi,
  // Go: r.Handle / r.HandleFunc / r.GET etc.
  /r\.\s*(?:Handle(?:Func)?|GET|POST|PUT|PATCH|DELETE)\s*\(\s*["']([^"']+)["']/gi,
  // Rails: get "/path"
  /\b(get|post|put|patch|delete)\s+["']([^"']+)["']/gi,
];

interface RouteMatch {
  lineIndex: number;
  method: string;
  path: string;
}

function extractRoutes(document: vscode.TextDocument): RouteMatch[] {
  const routes: RouteMatch[] = [];
  const text = document.getText();
  const lines = text.split('\n');

  const methodFromDecorator = (deco: string): string => {
    const m = deco.match(/\.(get|post|put|patch|delete|options|head)\s*\(/i);
    return m ? m[1].toUpperCase() : 'GET';
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Python decorators: @app.get("/path")
    const pyMatch = line.match(/@(?:\w+)\.(get|post|put|patch|delete|options|head)\s*\(\s*["']([^"']+)["']/i);
    if (pyMatch) {
      routes.push({ lineIndex: i, method: pyMatch[1].toUpperCase(), path: pyMatch[2] });
      continue;
    }

    // Express: router.get('/path')
    const expMatch = line.match(/(?:router|app|server)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*["']([^"']+)["']/i);
    if (expMatch) {
      routes.push({ lineIndex: i, method: expMatch[1].toUpperCase(), path: expMatch[2] });
      continue;
    }

    // Go: r.GET("/path")
    const goMatch = line.match(/r\s*\.\s*(GET|POST|PUT|PATCH|DELETE|Handle)\s*\(\s*["']([^"']+)["']/i);
    if (goMatch) {
      const m = goMatch[1].toUpperCase() === 'HANDLE' ? 'ANY' : goMatch[1].toUpperCase();
      routes.push({ lineIndex: i, method: m, path: goMatch[2] });
      continue;
    }

    // Rails: get '/path'
    const railsMatch = line.match(/^\s*(get|post|put|patch|delete)\s+["']([^"']+)["']/i);
    if (railsMatch) {
      routes.push({ lineIndex: i, method: railsMatch[1].toUpperCase(), path: railsMatch[2] });
    }
  }

  return routes;
}

function normalizePath(p: string): string {
  // Normalize trailing slash, convert :param to {param}
  return p.replace(/:([a-zA-Z_]+)/g, '{$1}').replace(/\/$/, '') || '/';
}

export function applyDecorations(
  editor: vscode.TextEditor,
  endpoints: CoverageEndpoint[],
): void {
  const routes = extractRoutes(editor.document);
  if (!routes.length) return;

  // Build lookup: "METHOD /path" → endpoint
  const lookup = new Map<string, CoverageEndpoint>();
  for (const ep of endpoints) {
    lookup.set(`${ep.method.toUpperCase()} ${normalizePath(ep.path)}`, ep);
  }

  const covered: vscode.DecorationOptions[] = [];
  const uncovered: vscode.DecorationOptions[] = [];

  for (const route of routes) {
    const key = `${route.method} ${normalizePath(route.path)}`;
    const ep  = lookup.get(key);
    const range = new vscode.Range(route.lineIndex, 0, route.lineIndex, 0);

    if (ep) {
      covered.push({
        range,
        renderOptions: {
          after: {
            contentText: `  ✓ ${ep.captures} capture${ep.captures !== 1 ? 's' : ''}${ep.has_tests ? ' · tested' : ''}`,
          },
        },
      });
    } else {
      uncovered.push({
        range,
        renderOptions: {
          after: { contentText: '  ○ no captures' },
        },
      });
    }
  }

  editor.setDecorations(COVERED_TYPE, covered);
  editor.setDecorations(UNCOVERED_TYPE, uncovered);
}

export function clearDecorations(editor: vscode.TextEditor): void {
  editor.setDecorations(COVERED_TYPE, []);
  editor.setDecorations(UNCOVERED_TYPE, []);
}
