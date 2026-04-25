/**
 * Httrace VS Code Extension — entry point.
 *
 * Commands registered:
 *   httrace.generateTests   — generate test files from captured traffic
 *   httrace.showDiff        — show API schema drift in the Output panel
 *   httrace.replayTraffic   — replay captures against a target URL
 *   httrace.showCoverage    — show coverage QuickPick + refresh tree view
 *   httrace.refreshCoverage — force a coverage refresh
 *   httrace.configure       — open Httrace settings
 *
 * Passive features:
 *   - Status bar item shows live endpoint + capture count
 *   - Coverage tree view in the Httrace activity bar panel
 *   - Inline "✓ N captures" decorations on route definitions
 *   - Background polling every N seconds (configurable)
 */

import * as vscode from "vscode";
import { CoverageProvider, EndpointCoverage } from "./coverageProvider";
import { CoverageTreeProvider } from "./coverage-tree";
import { generateTests }  from "./commands/generate";
import { showDiff }       from "./commands/diff";
import { replayTraffic }  from "./commands/replay";
import { applyDecorations, clearDecorations } from "./decorations/coverage";
import { HttptraceClient, CoverageEndpoint } from "./api";

const SUPPORTED_LANGS = new Set(["python", "javascript", "typescript", "go", "ruby"]);

// ── Module-level state ─────────────────────────────────────────────────────────

let legacyProvider: CoverageProvider;
let treeProvider: CoverageTreeProvider;
let statusBarItem: vscode.StatusBarItem;
let pollTimer: ReturnType<typeof setInterval> | undefined;
let lastEndpoints: CoverageEndpoint[] = [];

// ── Config helper ──────────────────────────────────────────────────────────────

function cfg() {
  const c = vscode.workspace.getConfiguration("httrace");
  return {
    apiKey:            c.get<string>("apiKey", "").trim(),
    apiUrl:            c.get<string>("apiUrl", "https://api.httrace.com"),
    serviceName:       c.get<string>("serviceName", "").trim(),
    pollInterval:      c.get<number>("pollIntervalSeconds", 60) * 1000,
    showInlineDecos:   c.get<boolean>("showInlineDecorations", true),
  };
}

// ── Coverage refresh ───────────────────────────────────────────────────────────

async function refreshCoverage(showNotification = false): Promise<void> {
  const { apiKey, apiUrl, serviceName } = cfg();
  if (!apiKey || !serviceName) {
    statusBarItem.text    = "$(beaker) Httrace: not configured";
    statusBarItem.tooltip = "Click to open Httrace settings";
    statusBarItem.command = "httrace.configure";
    statusBarItem.show();
    return;
  }

  try {
    statusBarItem.text = "$(sync~spin) Httrace";

    // Use legacy provider for coverage (tested, reliable)
    const result = await legacyProvider.fetchCoverage(apiUrl, apiKey, serviceName);
    lastEndpoints = result.endpoints as unknown as CoverageEndpoint[];

    treeProvider.update(serviceName, lastEndpoints);

    const epCount  = lastEndpoints.length;
    const capCount = result.total_captures;
    statusBarItem.text    = `$(beaker) ${epCount} endpoints · ${capCount} captures`;
    statusBarItem.tooltip = `Httrace: ${serviceName} — ${epCount} endpoints, ${capCount} captures\nLast refresh: ${new Date().toLocaleTimeString()}`;
    statusBarItem.command = "httrace.showCoverage";
    statusBarItem.show();

    // Apply inline decorations to the active editor
    const { showInlineDecos } = cfg();
    const editor = vscode.window.activeTextEditor;
    if (editor && showInlineDecos && SUPPORTED_LANGS.has(editor.document.languageId)) {
      applyDecorations(editor, lastEndpoints);
    }

    if (showNotification) {
      vscode.window.showInformationMessage(
        `Httrace: ${epCount} endpoint${epCount !== 1 ? "s" : ""} for ${serviceName} (${capCount} captures)`,
      );
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    statusBarItem.text    = "$(warning) Httrace";
    statusBarItem.tooltip = `Error: ${msg}`;
    statusBarItem.show();
    if (showNotification) {
      vscode.window.showErrorMessage(`Httrace: coverage fetch failed — ${msg}`);
    }
  }
}

// ── Coverage QuickPick ─────────────────────────────────────────────────────────

async function showCoverageQuickPick(): Promise<void> {
  const { apiKey, apiUrl, serviceName } = cfg();
  if (!apiKey || !serviceName) {
    const action = await vscode.window.showWarningMessage(
      "Httrace: configure your API key and service name first.",
      "Open Settings",
    );
    if (action === "Open Settings") {
      vscode.commands.executeCommand("httrace.configure");
    }
    return;
  }

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Httrace: fetching coverage…", cancellable: false },
    async () => {
      try {
        const result = await legacyProvider.fetchCoverage(apiUrl, apiKey, serviceName);
        lastEndpoints = result.endpoints as unknown as CoverageEndpoint[];
        treeProvider.update(serviceName, lastEndpoints);

        if (!result.endpoints.length) {
          vscode.window.showInformationMessage("Httrace: no endpoints captured yet.");
          return;
        }

        const items: vscode.QuickPickItem[] = result.endpoints
          .sort((a, b) => b.captures - a.captures)
          .map(ep => ({
            label:       `$(${ep.captures > 0 ? "pass" : "circle-outline"})  ${ep.method} ${ep.path}`,
            description: `${ep.captures} capture${ep.captures !== 1 ? "s" : ""}`,
            detail:      `Status codes: ${(ep.statuses as number[]).sort((x, y) => x - y).join(", ")}`,
          }));

        await vscode.window.showQuickPick(items, {
          title:             `Httrace Coverage — ${serviceName} (${result.total_captures} total)`,
          placeHolder:       "Search endpoints…",
          matchOnDescription: true,
          matchOnDetail:     true,
        });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`Httrace: ${msg}`);
      }
    },
  );
}

// ── Polling ────────────────────────────────────────────────────────────────────

function restartPolling(context: vscode.ExtensionContext): void {
  if (pollTimer !== undefined) clearInterval(pollTimer);

  const { apiKey, pollInterval } = cfg();
  if (!apiKey) return;

  void refreshCoverage(false);
  pollTimer = setInterval(() => {
    const editor = vscode.window.activeTextEditor;
    if (editor && SUPPORTED_LANGS.has(editor.document.languageId)) {
      void refreshCoverage(false);
    }
  }, pollInterval);
}

// ── Activation ─────────────────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext): void {
  legacyProvider = new CoverageProvider();
  treeProvider   = new CoverageTreeProvider();

  // ── Tree view ──────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.window.createTreeView("httrace.coverageView", {
      treeDataProvider: treeProvider,
      showCollapseAll: true,
    }),
  );

  // ── Status bar ─────────────────────────────────────────────────────────────
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text    = "$(beaker) Httrace";
  statusBarItem.command = "httrace.showCoverage";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // ── Commands ───────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("httrace.generateTests",  generateTests),
    vscode.commands.registerCommand("httrace.showDiff",       showDiff),
    vscode.commands.registerCommand("httrace.replayTraffic",  replayTraffic),
    vscode.commands.registerCommand("httrace.showCoverage",   showCoverageQuickPick),
    vscode.commands.registerCommand("httrace.refreshCoverage", () => refreshCoverage(true)),
    vscode.commands.registerCommand("httrace.configure", () =>
      vscode.commands.executeCommand("workbench.action.openSettings", "httrace"),
    ),
  );

  // ── Editor events ──────────────────────────────────────────────────────────
  context.subscriptions.push(
    // Re-apply decorations when switching tabs
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (!editor || !cfg().showInlineDecos) return;
      if (SUPPORTED_LANGS.has(editor.document.languageId) && lastEndpoints.length) {
        applyDecorations(editor, lastEndpoints);
      }
    }),

    // Re-apply after a save (code may have changed)
    vscode.workspace.onDidSaveTextDocument(doc => {
      const editor = vscode.window.activeTextEditor;
      if (editor?.document === doc && cfg().showInlineDecos && lastEndpoints.length) {
        applyDecorations(editor, lastEndpoints);
      }
    }),

    // Re-apply when visible editors change (split panes, etc.)
    vscode.window.onDidChangeVisibleTextEditors(editors => {
      if (!cfg().showInlineDecos || !lastEndpoints.length) return;
      for (const editor of editors) {
        if (SUPPORTED_LANGS.has(editor.document.languageId)) {
          applyDecorations(editor, lastEndpoints);
        }
      }
    }),

    // Restart polling when settings change
    vscode.workspace.onDidChangeConfiguration(e => {
      if (e.affectsConfiguration("httrace")) {
        legacyProvider.invalidate();
        restartPolling(context);
      }
    }),
  );

  restartPolling(context);
}

export function deactivate(): void {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
  // Clear decorations from all visible editors
  for (const editor of vscode.window.visibleTextEditors) {
    clearDecorations(editor);
  }
}
